"""
功能二 · 飞行时间窗口规划。

在期望日期内寻找湍流最低且全程安全的时间窗口；当日无窗口时向前推演若干天，
输出推荐时刻、湍流曲线、备用时段与预警提示。

性能策略（依据真实数据实测结论）：
  几何只算一次 —— 路径几何与采样点固定，扫描阶段只做「时间推进 + 查表」，
  绝不在 24 × N 天的循环里重复调用 A*。实测 8 天 × 24 槽位 × 120 采样点的
  纯计算耗时约 0.06s，瓶颈只在 NetCDF I/O，因此扫描只读 u/v/w 三个变量，
  温湿度预警等推荐窗口确定后再补读一次。

时区口径（重要）：
  实测 NetCDF 时间标签为 UTC（依据：温度日变化峰值时刻对经度回归斜率
  -0.0663 h/deg，与理论 -1/15 吻合；且峰值相位反推当地 14.3h 成立）。
  UI 输入按北京时间理解。

  实现约定：模块内部一律使用【数据时间】(= UTC 标签) 做索引与比较，
  仅在生成对外的时刻字符串时用 to_user_time() 换算回北京时间。
  这样可避免「用本地时间标签去查 UTC 时间轴」造成的 8 小时整体偏移。
"""
from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Tuple
import math
import time

import numpy as np

from config import settings
import timebase
from data_loader import data_loader
from planner import (plan_path_ex, PlanResult, search_deadline,
                     _haversine_km, SEGMENT_PAD_DEG)
from risk import classify_point, LEVEL_LOW, LEVEL_UNFLYABLE
from route_options import summarize_plan
import geo

# 不可飞行原因
REASON_NO_SAFE_SLOT = "no_safe_slot"                        # 全天 / 推演期内无安全时段
REASON_PERMANENTLY_BLOCKED = "route_permanently_blocked"    # 航路本身被地形或 NaN 区封死
REASON_DATA_MISSING = "data_coverage_insufficient"          # 起降点超出数据覆盖范围
REASON_OD_TOO_CLOSE = "od_too_close"          # 起终点重合到离散不出两点，构不成航线

# 扫描场 bbox 外扩（度）。取 A* 自读时的同一口径：功能二把这份场注入给规划器复用，
# 场比 A* 自己的搜索框还小就必然触发越界回退，注入也就省不下任何一次读盘。
_BBOX_PAD_DEG = SEGMENT_PAD_DEG


@dataclass
class SlotResult:
    """单个候选起飞时刻的扫描结果。"""
    index: int                      # 槽位序号（自 0 起）
    data_time: np.datetime64        # 起飞时刻（数据时间 = UTC 标签）
    safe: bool
    max_tke: Optional[float]
    max_edr: Optional[float]
    level: str

    @property
    def user_label(self) -> str:
        """展示用的北京时间 'HH:MM'。"""
        return _fmt_hhmm(self.data_time)

    @property
    def minute_of_day(self) -> int:
        """起飞时刻在北京时间当日的分钟数（0–1439）。"""
        return int(to_user_time(self.data_time).astype('datetime64[m]').astype(int) % (24 * 60))


@dataclass
class Window:
    """由相邻安全槽位合并成的连续时间窗口。"""
    start_index: int
    end_index: int
    start_label: str
    end_label: str
    level: str
    max_tke: float
    max_edr: float
    best_slot: int      # 窗口内风险最优的槽位序号


# ==================== 时区与格式工具 ====================

# 时区换算统一收敛到 timebase 模块，避免各模块各写一套造成口径漂移。
# 保留 to_data_time / to_user_time 两个别名，兼容既有调用方与测试。
to_data_time = timebase.to_data_time
to_user_time = timebase.to_user_time


def _fmt_hhmm(data_time) -> str:
    """数据时间 -> 北京时间 'HH:MM'。"""
    t = to_user_time(data_time)
    hour = int(t.astype('datetime64[h]').astype(int) % 24)
    minute = int(t.astype('datetime64[m]').astype(int) % 60)
    return f"{hour:02d}:{minute:02d}"


def nearest_time_index(times: np.ndarray, targets: np.ndarray) -> np.ndarray:
    """在有序时间轴上为每个目标时刻找最近索引（向量化）。"""
    times = np.asarray(times)
    targets = np.asarray(targets)
    if len(times) == 0:
        raise ValueError("empty time axis")
    if len(times) == 1:
        return np.zeros(targets.shape, dtype=int)
    pos = np.searchsorted(times, targets)
    pos = np.clip(pos, 1, len(times) - 1)
    left = times[pos - 1]
    right = times[pos]
    take_left = (targets - left) <= (right - targets)
    return np.where(take_left, pos - 1, pos).astype(int)


def _to_pts(path) -> List[Tuple[float, float]]:
    """把 path 归一化为 [(lat, lon), ...]。"""
    pts: List[Tuple[float, float]] = []
    for p in path:
        if isinstance(p, dict):
            pts.append((float(p["lat"]), float(p["lon"])))
        else:
            pts.append((float(p.lat), float(p.lon)))
    return pts


def _cum_km(lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
    cum = np.zeros(len(lats))
    for i in range(1, len(lats)):
        cum[i] = cum[i - 1] + _haversine_km(lats[i - 1], lons[i - 1], lats[i], lons[i])
    return cum


def sample_along_path(path, step_deg: Optional[float] = None, nodes_only: bool = False,
                      snap_lats: Optional[np.ndarray] = None,
                      snap_lons: Optional[np.ndarray] = None):
    """构造沿航线的评估采样点。

    path 为 [{'lat','lon'}, ...] 或带 .lat/.lon 属性的对象序列。

    参数
    ----
    nodes_only : True 时直接使用 path 自带的节点作为采样点，不做插值。

        必须这样做的原因（实测缺陷）：A* 的路径节点是逐格扩展得到的、
        且每个节点都通过硬约束校验；若在两个相邻节点之间按固定步长插值，
        插值点会因「斜向穿越格子角落」而吸附到路径并未经过的相邻格点上，
        其中可能是 NaN（地形）或高湍流格点，导致本来安全的航线被误判为
        「全天不可飞 / 永久阻塞」。因此规划器给出的路径必须按节点采样。
    snap_lats / snap_lons : 数据网格的纬度/经度数组。给出时，插值点会吸附到
        最近的格点中心并去除连续重复，保证评估的是真实格点（仅用于直线兜底）。
    step_deg : 插值步长，默认走 config。

    返回 (lats, lons, cum_km, hit_flags)。
    """
    pts = _to_pts(path)
    empty = (np.array([]), np.array([]), np.array([]), np.array([], dtype=bool))
    if len(pts) < 2:
        return empty

    if nodes_only:
        resampled = [(round(lat, 4), round(lon, 4)) for lat, lon in pts]
    else:
        step_deg = max(1e-3, float(step_deg or settings.TW_SAMPLE_STEP_DEG))
        resampled = []
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            span = max(abs(b[0] - a[0]), abs(b[1] - a[1]))
            n = max(1, int(math.ceil(span / step_deg)))
            for k in range(n):
                frac = k / n
                resampled.append((a[0] + (b[0] - a[0]) * frac,
                                  a[1] + (b[1] - a[1]) * frac))
        resampled.append(pts[-1])

        if snap_lats is not None and snap_lons is not None and len(snap_lats) and len(snap_lons):
            snapped: List[Tuple[float, float]] = []
            for lat, lon in resampled:
                i_la = int(np.abs(np.asarray(snap_lats) - lat).argmin())
                i_lo = int(np.abs(np.asarray(snap_lons) - lon).argmin())
                cell = (round(float(snap_lats[i_la]), 4), round(float(snap_lons[i_lo]), 4))
                if not snapped or snapped[-1] != cell:
                    snapped.append(cell)
            if len(snapped) >= 2:
                resampled = snapped

    lats = np.array([p[0] for p in resampled])
    lons = np.array([p[1] for p in resampled])
    cum = _cum_km(lats, lons)

    hit = np.zeros(len(resampled), dtype=bool)
    for i, (lat, lon) in enumerate(resampled):
        for p in pts:
            if abs(p[0] - lat) < 1e-3 and abs(p[1] - lon) < 1e-3:
                hit[i] = True
                break
    return lats, lons, cum, hit


# ==================== 纯计算层（可脱离真实数据测试） ====================

def scan_slots(tke: np.ndarray, edr: np.ndarray, times: np.ndarray,
               sample_lats: np.ndarray, sample_lons: np.ndarray,
               slab_lats: np.ndarray, slab_lons: np.ndarray,
               cum_km: np.ndarray, speed: float,
               day_start_data: np.datetime64, n_slots: int, step_minutes: int,
               tke_th: float, edr_th: float) -> List[SlotResult]:
    """扫描一天内各候选起飞时刻的全程安全性。

    参数中的 times / day_start_data 均为【数据时间】(UTC 标签)。
    对每个候选起飞时刻 t0，第 i 个采样点的到达时刻为
        t_i = t0 + cum_km[i] / speed   （小时）
    再对该时刻表做向量化查表，判定全程是否满足硬约束。

    NaN（地形 / 数据缺失）视为不安全，与 planner 的邻居判定口径一致。
    """
    n_samples = len(cum_km)
    if n_samples == 0 or tke is None or np.asarray(tke).size == 0:
        return []

    tke = np.asarray(tke)
    edr = np.asarray(edr)
    times = np.asarray(times)

    idx_lat = np.abs(slab_lats[:, None] - sample_lats[None, :]).argmin(axis=0)
    idx_lon = np.abs(slab_lons[:, None] - sample_lons[None, :]).argmin(axis=0)

    offset_h = cum_km / float(speed) if speed else np.zeros(n_samples)
    offsets = np.rint(offset_h * 3600.0).astype('timedelta64[s]')

    slot_starts = [day_start_data + np.timedelta64(int(i * step_minutes), 'm')
                   for i in range(int(n_slots))]
    if not slot_starts:
        return []

    # 一次构造 (n_slots, n_samples) 的到达时刻矩阵，整体查表
    t_mat = np.empty((len(slot_starts), n_samples), dtype='datetime64[ns]')
    for s, base in enumerate(slot_starts):
        t_mat[s] = base + offsets

    idx_time = nearest_time_index(times, t_mat.ravel()).reshape(t_mat.shape)
    tke_vals = tke[idx_time, idx_lat[None, :], idx_lon[None, :]]
    edr_vals = edr[idx_time, idx_lat[None, :], idx_lon[None, :]]

    # 越界防护：最近邻索引会静默把超出 slab 范围的时刻吸附到边界时刻，
    # 使尾部槽位得到重复数值（实测出现过 4 个槽位数值全等的假象）。
    # 这里显式标记越界为不可评估，而不是拿边界值冒充真实数值。
    span = np.timedelta64(30, 'm')   # 容忍半个步长（时间轴为整点标签）
    lo_t = np.datetime64(times[0]) - span
    hi_t = np.datetime64(times[-1]) + span
    in_range = (t_mat >= lo_t) & (t_mat <= hi_t)

    results: List[SlotResult] = []
    for s, base in enumerate(slot_starts):
        if not bool(in_range[s].all()):
            results.append(SlotResult(s, base, False, None, None, LEVEL_UNFLYABLE))
            continue
        tv, ev = tke_vals[s], edr_vals[s]
        finite = bool(np.isfinite(tv).all() and np.isfinite(ev).all())
        if finite:
            max_tke = float(np.max(tv))
            max_edr = float(np.max(ev))
            safe = bool(max_tke < tke_th and max_edr < edr_th)
            level = classify_point(max_tke, max_edr, tke_th, edr_th)
            results.append(SlotResult(s, base, safe, round(max_tke, 4), round(max_edr, 4), level))
        else:
            results.append(SlotResult(s, base, False, None, None, LEVEL_UNFLYABLE))
    return results


def aggregate_windows(slots: List[SlotResult]) -> List[Window]:
    """把相邻的安全槽位合并为连续时间窗口。"""
    windows: List[Window] = []
    run_start = None
    for i, s in enumerate(slots):
        if s.safe and run_start is None:
            run_start = i
        elif not s.safe and run_start is not None:
            windows.append(_make_window(slots, run_start, i - 1))
            run_start = None
    if run_start is not None:
        windows.append(_make_window(slots, run_start, len(slots) - 1))
    return windows


def _make_window(slots: List[SlotResult], a: int, b: int) -> Window:
    seg = slots[a:b + 1]
    # 窗口内风险最优槽位：最大 TKE 最低者，平局取较早。
    # 该槽位即「推荐起飞时刻」。
    best = min(seg, key=lambda s: (s.max_tke if s.max_tke is not None else float('inf'), s.index))
    level = max((s.level for s in seg), key=_level_rank)
    return Window(
        start_index=a,
        end_index=b,
        start_label=seg[0].user_label,
        end_label=seg[-1].user_label,
        level=level,
        max_tke=max((s.max_tke for s in seg if s.max_tke is not None), default=0.0),
        max_edr=max((s.max_edr for s in seg if s.max_edr is not None), default=0.0),
        best_slot=best.index,
    )


_LEVEL_ORDER = {LEVEL_LOW: 0, "中风险": 1, "高风险": 2, LEVEL_UNFLYABLE: 3}


def _level_rank(level: str) -> int:
    return _LEVEL_ORDER.get(level, 3)


def pick_recommended(windows: List[Window], slots: Optional[List[SlotResult]] = None) -> Optional[Window]:
    """选择推荐窗口。

    排序口径（需求原文为「寻找湍流强度最低的窗口期」）：
      主键 = 窗口内「最优槽位」的风险（即该窗口里最好的起飞时刻有多好），
      次键 = 窗口整体最差风险，再次 = 起始时刻（平局取较早）。

    不能用「窗口整体 최差风险」作主键：一个只包含单个高风险槽位的短窗口
    （如 00:00 高风险 0.86）会击败一个包含低风险时段的长窗口
    （如 02:00–23:00，其中 06:00 仅 0.27），从而把用户引向更差的起飞时刻。
    """
    if not windows:
        return None
    if slots:
        def key(w: Window):
            best = slots[w.best_slot]
            best_tke = best.max_tke if best.max_tke is not None else float('inf')
            return (best_tke, w.max_tke, w.start_index)
        return min(windows, key=key)
    return min(windows, key=lambda w: (w.max_tke, w.start_index))


def rank_backup_windows(windows: List[Window], exclude: Optional[Window],
                        limit: Optional[int] = None) -> List[Window]:
    """排序备用时段：风险升序，平局取较早；排除推荐窗口自身。"""
    limit = int(limit if limit is not None else settings.TW_MAX_BACKUP_WINDOWS)
    rest = [w for w in windows if exclude is None or w.start_index != exclude.start_index]
    rest.sort(key=lambda w: (w.max_tke, w.start_index))
    return rest[:limit]


# ==================== 编排层 ====================

def _user_pair(data_time) -> Tuple[str, str]:
    """数据时刻（UTC 标签）-> (北京日期, 北京时刻)。

    PlanRequest 的 date / start_time 是北京时间口径，而候选起飞时刻来自数据时间轴，
    跨午夜时必须连日期一起换 —— 只换时刻会让整条航线偏 8 小时。
    """
    s = str(np.datetime64(to_user_time(data_time), 'm'))     # '2022-07-02T05:00'
    return s[:10], s[11:16]


def _plan_one_candidate(req, slot, straight_km, field=None,
                        deadline=None) -> Dict[str, Any]:
    """按候选自己的起飞时刻规划一条真实航线（Top-K 两遍里的第二遍）。

    只按「平衡」档规划：Top-K 已经乘过一次成本，再乘三档不值；
    切档回功能一，那里有完整的三档评估。

    field 是本函数第一遍已经读好的扫描场，注入后这一遍不再碰 NetCDF。
    deadline 是整次请求共用的墙钟时刻（见 planner.search_deadline）：余额耗尽的候选
    会在 256 次展开内就停，仍然留在结果里标「未算出」，不悄悄消失。
    """
    from schemas import PlanRequest
    date, hhmm = _user_pair(slot.data_time)
    plan = plan_path_ex(PlanRequest(
        start=req.start, end=req.end, waypoints=req.waypoints or [],
        date=date, start_time=hhmm, speed=req.speed,
        tke_threshold=req.tke_threshold, edr_threshold=req.edr_threshold,
        priority=settings.DEFAULT_PRIORITY,
    ), field=field, deadline=deadline)
    return summarize_plan(plan, straight_km, float(req.speed or 0.0))


def _top_k_candidates(slots: List[SlotResult], recommended: Window,
                      backups: List[Window], k: int) -> List[Tuple[Window, SlotResult]]:
    """第二遍要规划的候选：推荐窗口 + 备用时段，各取其窗口内风险最优的槽位。

    全天都安全时这些会合并成一整个窗口，Top-K 就只剩 1 条候选 —— 而用户问的恰恰是
    「我要 14:00 起飞，那条线长什么样」。所以候选不足 K 时，在推荐窗口内再均匀取几个
    起飞时刻补齐：它们同属一个安全窗口，区别只是给用户多条可以照着飞的线。
    """
    out: List[Tuple[Window, SlotResult]] = []
    taken = set()
    for win in [recommended] + list(backups):
        if len(out) >= k:
            break
        out.append((win, slots[win.best_slot]))
        taken.add(win.best_slot)

    if len(out) < k:
        pool = [i for i in range(recommended.start_index, recommended.end_index + 1)
                if i not in taken]
        need = k - len(out)
        if pool:
            step = max(1, len(pool) // need)
            for i in pool[::step][:need]:
                out.append((recommended, slots[i]))
    return out


def plan_top_k_routes(req, slots: List[SlotResult], windows: List[Window],
                      recommended: Window, backups: List[Window],
                      straight_km: float, field=None, deadline=None) -> List[Dict[str, Any]]:
    """对 Top-K 候选各按【自身起飞时刻】规划一条航线，并做第二遍（细）排序。

    第一遍是「一条固定走廊 × 24 个槽位」的查表，快但带时刻偏置：
    在 08:00 的路 上评 21:00 安不安全。第二遍只给进了 Top-K 的少数候选补上
    它们各自该走的那条线，于是推荐依据从「走廊级 max_tke」换成「真实航线」。

    不可飞的候选照样留在结果里（带 reason）—— 悄悄消失会让人以为
    「那条备用时段没风险，只是没排上」。

    field 把第一遍的扫描场交给 A* 复用：每个候选各自读一次 31 帧的话，
    Top-K=4 会把接口从 1 秒拖到 3 秒以上，而这份场本来就在工作内存里。
    """
    k = max(1, int(settings.TW_TOP_K_ROUTES))
    routes: List[Dict[str, Any]] = []
    for win, slot in _top_k_candidates(slots, recommended, backups, k):
        summary = _plan_one_candidate(req, slot, straight_km, field=field, deadline=deadline)
        routes.append({
            **summary,
            "takeoff": str(to_user_time(slot.data_time))[:16],
            "takeoff_data": slot.data_time,
            "window": [win.start_label, win.end_label],
            "scan_level": slot.level,
            "scan_max_tke": slot.max_tke,
            "priority": settings.DEFAULT_PRIORITY,
        })
    return rank_routes(routes)


def rank_routes(routes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """细排：可飞 > 整体风险档 > 沿程最坏点 > 里程 > 起飞时刻。

    刻意不把「风险」与「里程」混成一个加权键 ——
    绕得更远的线风险可能更高，混排会产出「推荐那条反而更危险」的结论。

    「沿程最坏点」必须排在「里程」前面：一条走廊全天平静的日子里四条候选线
    几何完全相同、里程也一样，此时若直接比起飞时刻，冠军会因为时钟而跳到半夜，
    把走廊级那个更有信息量的信号（谁的 TKE 峰值更低）白白丢掉。
    """
    def key(r: Dict[str, Any]):
        km = r["total_distance"] if r["feasible"] else float('inf')
        peak = r["max_tke"] if r.get("max_tke") is not None else float('inf')
        return (0 if r["feasible"] else 1,
                _level_rank(r.get("overall_level") or ""),
                peak,
                km,
                r["takeoff"])
    return sorted(routes, key=key)


def _route_dict(r: Dict[str, Any]) -> Dict[str, Any]:
    """输出给前端的航线字典。

    剥掉内部字段（takeoff_data），并把 overall_level 改名成 schemas 里的 planned_level：
    功能一/风险模块统一叫 overall_level，功能二的响应要跟走廊级 scan_level 成对，
    不在这一步对齐的话，pydantic 会把未知键丢掉、前端拿到的风险等级是空的。
    """
    out = {k: v for k, v in r.items() if k != "takeoff_data"}
    if "overall_level" in out:
        out["planned_level"] = out.pop("overall_level")
    return out


def assess_time_window(req, run_planner: bool = True, deadline=None) -> Dict[str, Any]:
    """执行功能二评估。req 需为 schemas.TimeWindowRequest。

    deadline：整次请求共用的 `time.monotonic()` 绝对时刻。功能二一次要点 Top-K 个候选，
    每个候选都是一次全程规划，不设共用的余额就会「一个慢候选拖死整张表」。
    不给则本函数自取 ASTAR_REQUEST_TIME_LIMIT_S。
    """
    if deadline is None:
        deadline = search_deadline(settings.ASTAR_REQUEST_TIME_LIMIT_S)
    speed = float(req.speed or 60.0)
    tke_th = req.tke_threshold if req.tke_threshold is not None else settings.TKE_THRESHOLD_HIGH
    edr_th = req.edr_threshold if req.edr_threshold is not None else settings.EDR_THRESHOLD_HIGH

    start = (float(req.start.lat), float(req.start.lon))
    end = (float(req.end.lat), float(req.end.lon))

    step_hours = _step_hours(req)
    step_minutes = int(step_hours * 60)
    n_slots = max(1, min(int(24 * 60 // step_minutes), int(settings.TW_MAX_SCAN_SLOTS)))

    meta: Dict[str, Any] = {
        "data_label_is_utc": bool(settings.DATA_LABEL_IS_UTC),
        "tz_offset_hours": int(settings.DATA_TZ_OFFSET_HOURS),
        "scan_step_hours": int(step_hours),
        "tke_threshold": float(tke_th),
        "edr_threshold": float(edr_th),
    }

    # 1) 数据覆盖检查
    if not (data_loader.check_coverage(start[0], start[1])
            and data_loader.check_coverage(end[0], end[1])):
        return _empty_response(req, REASON_DATA_MISSING, meta)

    # 2) 几何基线：优先复用规划路径，失败退回直线离散化
    path_pts, fallback = _geometry(start, end, req, run_planner, deadline=deadline)
    meta["geometry_fallback"] = bool(fallback)

    raw = _to_pts(path_pts)
    if len(raw) < 2:
        return _empty_response(req, REASON_OD_TOO_CLOSE, meta)

    raw_lats = np.array([p[0] for p in raw])
    raw_lons = np.array([p[1] for p in raw])
    # 兜底几何只有起终点，途经点会被漏出 bbox；而注入给 A* 的场必须罩住每一段
    # 的搜索框（途经点前后各 SEGMENT_PAD_DEG），所以把途经点也计入取框范围。
    wp = req.waypoints or []
    lat_ext = [float(raw_lats.min()), float(raw_lats.max())] + [float(p.lat) for p in wp]
    lon_ext = [float(raw_lons.min()), float(raw_lons.max())] + [float(p.lon) for p in wp]
    bbox = (min(lat_ext) - _BBOX_PAD_DEG, max(lat_ext) + _BBOX_PAD_DEG,
            min(lon_ext) - _BBOX_PAD_DEG, max(lon_ext) + _BBOX_PAD_DEG)

    # 3) 用户日期 -> 数据时间；扫描窗口覆盖「北京时间的这一天」
    day_start_user = np.datetime64(f"{req.date}T00:00:00")
    day_start_data = to_data_time(day_start_user)

    forecast_days = int(req.forecast_days if req.forecast_days is not None
                        else settings.TW_FORECAST_DAYS)
    forecast_days = max(0, min(forecast_days, int(settings.TW_FORECAST_DAYS)))

    # slab 必须覆盖「最后一个候选槽位起飞 + 整个飞行时长 + 曲线外扩」。
    # 否则末段采样点的到达时刻会落到 slab 之外，被最近邻索引静默吸附到
    # 边界时刻，使尾部若干槽位得到完全相同的数值（实测出现过 4 个槽位
    # 数值全等 0.3815 的假象），导致评估失真。
    raw_cum = _cum_km(raw_lats, raw_lons)
    est_flight_h = float(raw_cum[-1]) / speed if speed else 0.0
    pad_h = int(settings.TW_CURVE_PAD_HOURS)
    total_hours = int((forecast_days + 1) * 24 + math.ceil(est_flight_h) + pad_h + 2)
    tke, edr, slab_lats, slab_lons, slab_times = _load_turbulence(
        day_start_data, total_hours, bbox)
    if tke is None:
        return _empty_response(req, REASON_DATA_MISSING, meta)

    # 3b) 构造采样点
    #     规划路径 -> 直接用其网格节点（避免插值吸附到未经过的邻格，见 sample_along_path）
    #     直线兜底 -> 插值后吸附到真实格点
    lats, lons, cum, _hit = (
        sample_along_path(path_pts, nodes_only=True) if not fallback
        else sample_along_path(path_pts, snap_lats=slab_lats, snap_lons=slab_lons)
    )
    if len(cum) < 2:
        return _empty_response(req, REASON_OD_TOO_CLOSE, meta)

    route_km = float(cum[-1])
    flight_time_h = route_km / speed if speed else 0.0
    meta.update({"route_km": round(route_km, 1),
                 "flight_time_h": round(flight_time_h, 2),
                 "n_samples": int(len(cum))})

    # 3c) 几何本身的永久阻塞判定（地形 / 数据缺失区）
    geom_blocked = geometry_blocked(tke, edr, slab_times, slab_lats, slab_lons, lats, lons)
    meta["geometry_blocked"] = bool(geom_blocked)

    # 4) 当日扫描（第 d 个北京日对应的数据时间区间为 [day_start_data+d, +24h)）
    extra_h = int(math.ceil(est_flight_h)) + int(settings.TW_CURVE_PAD_HOURS) + 1
    day0 = _scan_one_day(tke, edr, slab_times, lats, lons, slab_lats, slab_lons,
                         cum, speed, day_start_data, 0, n_slots, step_minutes,
                         tke_th, edr_th, extra_h)
    slots = day0["slots"]
    windows = day0["windows"]
    recommended = pick_recommended(windows, slots)
    slot_analysis = [_slot_dict(s) for s in slots]

    if recommended is not None:
        # ---- 情况一：当日存在安全时间窗口 ----
        rec = slots[recommended.best_slot]
        takeoff_data = rec.data_time
        backups = rank_backup_windows(windows, recommended)
        meta["window_count"] = len(windows)

        # 第二遍：Top-K 候选各按自身起飞时刻规划真实航线，再按真实航线重排。
        # 曲线与预警跟着【细排冠军】走 —— 跟着粗排结果算，就会出现
        # 「推荐 09:00、曲线画的却是 08:00 那条走廊」。
        straight_km = _haversine_km(start[0], start[1], end[0], end[1])
        t_k = time.perf_counter()
        routes = plan_top_k_routes(req, slots, windows, recommended, backups, straight_km,
                                   field=(tke, edr, slab_lats, slab_lons, slab_times),
                                   deadline=deadline)
        meta["top_k_planned"] = len(routes)
        meta["top_k_cost_ms"] = int((time.perf_counter() - t_k) * 1000)
        meta["top_k_field"] = "injected"
        meta["route_priority"] = settings.DEFAULT_PRIORITY

        best = routes[0]
        best_takeoff = best["takeoff_data"]
        best_flight_h = float(best.get("flight_time_h") or flight_time_h)
        landing_data = best_takeoff + np.timedelta64(int(round(best_flight_h * 3600)), 's')

        warnings = _warnings(lats, lons, best_takeoff, landing_data, bbox)

        return {
            "date": req.date,
            "has_window": True,
            "recommended": {
                "takeoff": str(to_user_time(takeoff_data))[:16],
                "window": [recommended.start_label, recommended.end_label],
                "landing": str(to_user_time(
                    takeoff_data + np.timedelta64(int(round(flight_time_h * 3600)), 's')))[:16],
                "level": rec.level,
                "max_tke": rec.max_tke,
                "max_edr": rec.max_edr,
            },
            "recommended_final": {
                "takeoff": best["takeoff"],
                "window": best["window"],
                "landing": str(to_user_time(landing_data))[:16],
                "level": best.get("overall_level") or LEVEL_UNFLYABLE,
                "max_tke": best.get("max_tke"),
                "max_edr": best.get("max_edr"),
            },
            "routes": [_route_dict(r) for r in routes],
            "backup_windows": [_window_dict(w) for w in backups],
            "warnings": warnings,
            "slot_analysis": slot_analysis,
            "forecast": {"enabled": False, "days": []},
            "reason": None,
            "meta": meta,
        }

    # ---- 情况二：当日无安全窗口 -> 向前推演 ----
    forecast_out: List[Dict[str, Any]] = []
    for d in range(1, forecast_days + 1):
        if (d + 1) * 24 + extra_h > len(slab_times):
            break
        day_d = _scan_one_day(tke, edr, slab_times, lats, lons, slab_lats, slab_lons,
                              cum, speed, day_start_data, d, n_slots, step_minutes,
                              tke_th, edr_th, extra_h)
        slots_d = day_d["slots"]
        best_d = pick_recommended(day_d["windows"], slots_d)
        date_label = str(day_start_user + np.timedelta64(d, 'D'))[:10]
        forecast_out.append({
            "date": date_label,
            "has_window": best_d is not None,
            "best_window": [best_d.start_label, best_d.end_label] if best_d else None,
            "level": (slots_d[best_d.best_slot].level if best_d else None),
            "max_tke": (slots_d[best_d.best_slot].max_tke if best_d else None),
            "safe_slot_count": sum(1 for s in slots_d if s.safe),
        })

    first_ok = next((d for d in forecast_out if d["has_window"]), None)
    # 只有在「几何本身被永久封死」时才报永久阻塞；否则是全天无安全时段
    reason = REASON_PERMANENTLY_BLOCKED if geom_blocked else REASON_NO_SAFE_SLOT

    return {
        "date": req.date,
        "has_window": False,
        "recommended": None,
        "backup_windows": [],
        "warnings": [],
        "slot_analysis": slot_analysis,
        "forecast": {
            "enabled": True,
            "days": forecast_out,
            "first_available_date": first_ok["date"] if first_ok else None,
        },
        "reason": reason,
        "meta": meta,
    }


def _scan_one_day(tke, edr, slab_times, lats, lons, slab_lats, slab_lons,
                  cum, speed, day_start_data, day_offset: int,
                  n_slots: int, step_minutes: int, tke_th: float, edr_th: float,
                  extra_hours: int = 0):
    """扫描第 day_offset 个「北京日」（相对 day_start_data）。

    extra_hours：该日 24 小时之外还要额外提供的小时数。
    必须传入（至少为飞行时长向上取整），否则当日起飞时刻较晚的槽位，
    其末段采样点的到达时刻会落到切片之外，被显式判为不可评估，
    表现为「傍晚起飞的槽位全部没有数值」。
    """
    s0 = day_offset * 24
    s1 = min(s0 + 24 + int(extra_hours), len(slab_times))
    slots = scan_slots(
        tke[s0:s1], edr[s0:s1], slab_times[s0:s1],
        lats, lons, slab_lats, slab_lons, cum, speed,
        day_start_data + np.timedelta64(day_offset, 'D'),
        n_slots, step_minutes, tke_th, edr_th,
    )
    return {"slots": slots, "windows": aggregate_windows(slots)}


def _step_hours(req) -> int:
    try:
        v = int(req.scan_step_hours) if getattr(req, "scan_step_hours", None) else settings.TW_SCAN_STEP_HOURS
    except Exception:
        v = settings.TW_SCAN_STEP_HOURS
    return max(1, min(v, 24))


def _load_turbulence(data_start, total_hours, bbox):
    """读取 TKE / EDR 场。口径统一走 meteorology 单入口，失败返回全 None。"""
    return data_loader.read_turbulence_field(data_start, total_hours, bbox)


def _geometry(start, end, req, run_planner: bool, deadline=None):
    """确定扫描用的路径几何：优先用规划器，失败退回直线离散化。"""
    if run_planner:
        try:
            from schemas import PlanRequest
            plan_req = PlanRequest(
                start=req.start, end=req.end, waypoints=req.waypoints or [],
                date=req.date, start_time=req.start_time or "08:00", speed=req.speed,
                tke_threshold=req.tke_threshold, edr_threshold=req.edr_threshold,
            )
            plan: PlanResult = plan_path_ex(plan_req, deadline=deadline)
            if plan.found and len(plan.path) >= 2:
                return [{"lat": p.lat, "lon": p.lon} for p in plan.path], False
        except Exception:
            pass
    return ([{"lat": start[0], "lon": start[1]}, {"lat": end[0], "lon": end[1]}], True)


def geometry_blocked(tke, edr, times, slab_lats, slab_lons,
                     sample_lats, sample_lons) -> bool:
    """判断航路几何本身是否被永久封死（穿越全天候缺失的格点）。

    直接检查几何而不是从扫描结果反推：采样点若在 slab 覆盖的【所有】时次
    都是 NaN，说明该处是地形或数据缺失区，任何起飞时刻都绕不开它，
    这才是「航路本身永久阻塞」。反之若只是因为某些时次湍流超标，
    属于「全天无安全时段」，两者对用户的含义完全不同。

    这样也可避免把越界等其它导致数值缺失的情况误判为永久阻塞。
    """
    if tke is None or np.asarray(tke).size == 0 or len(sample_lats) == 0:
        return False

    tke = np.asarray(tke)
    edr = np.asarray(edr)
    idx_lat = np.abs(slab_lats[:, None] - sample_lats[None, :]).argmin(axis=0)
    idx_lon = np.abs(slab_lons[:, None] - sample_lons[None, :]).argmin(axis=0)

    tke_s = tke[:, idx_lat, idx_lon]
    edr_s = edr[:, idx_lat, idx_lon]
    dead = np.isnan(tke_s).all(axis=0) | np.isnan(edr_s).all(axis=0)
    return bool(dead.any())


def _warnings(lats, lons, takeoff_data, landing_data, bbox) -> List[Dict[str, Any]]:
    """温湿度与风速预警（只读一次 tc/rh/u/v，仅推荐窗口时段）。"""
    out: List[Dict[str, Any]] = []
    span_h = max(1.0, float((landing_data - takeoff_data) / np.timedelta64(1, 'h')))
    hours = int(math.ceil(span_h)) + int(settings.TW_CURVE_PAD_HOURS) + 1

    tc_v, _, _, _ = data_loader.read_slab("tc", takeoff_data, hours, bbox)
    rh_v, _, _, _ = data_loader.read_slab("rh", takeoff_data, hours, bbox)
    u_v, _, _, _ = data_loader.read_slab("u", takeoff_data, hours, bbox)
    v_v, _, _, _ = data_loader.read_slab("v", takeoff_data, hours, bbox)

    if rh_v is not None and np.isfinite(rh_v).any():
        hi = float(np.nanmax(rh_v))
        if hi > settings.WARN_RH_ICE:
            out.append({"type": "humidity", "level": "warning",
                        "text": "相对湿度超过 90%，可能存在结冰 / 云中飞行风险",
                        "value": round(hi, 2)})
        elif hi > settings.WARN_RH_RAIN:
            out.append({"type": "humidity", "level": "info",
                        "text": "相对湿度超过 80%，可能出现降水"
                                "（数据集无降水变量，湿度为代理指标）",
                        "value": round(hi, 2)})

    if tc_v is not None and np.isfinite(tc_v).any():
        lo = float(np.nanmin(tc_v))
        if lo < settings.WARN_TEMP_LOW:
            out.append({"type": "temperature", "level": "warning",
                        "text": f"航路 500 hPa（约 5 km 高度）温度低于 "
                                f"{settings.WARN_TEMP_LOW:.0f} ℃，注意低温对电池性能的影响",
                        "value": round(lo, 2)})

    if u_v is not None and v_v is not None:
        sp = np.sqrt(np.asarray(u_v) ** 2 + np.asarray(v_v) ** 2)
        if np.isfinite(sp).any() and float(np.nanmax(sp)) > settings.WARN_WIND_HIGH:
            out.append({"type": "wind", "level": "info",
                        "text": f"航路高空风速超过 {settings.WARN_WIND_HIGH:.0f} m/s，"
                                f"注意航时与航向偏差",
                        "value": round(float(np.nanmax(sp)), 2)})
    return out


def _slot_dict(s: SlotResult) -> Dict[str, Any]:
    return {
        "time": s.user_label,
        "max_tke": s.max_tke,
        "max_edr": s.max_edr,
        "level": s.level,
        "safe": s.safe,
    }


def _window_dict(w: Window) -> Dict[str, Any]:
    return {
        "start": w.start_label,
        "end": w.end_label,
        "level": w.level,
        "max_tke": w.max_tke,
        "max_edr": w.max_edr,
    }


def _empty_response(req, reason: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "date": req.date,
        "has_window": False,
        "recommended": None,
        "backup_windows": [],
        "warnings": [],
        "slot_analysis": [],
        "forecast": {"enabled": False, "days": []},
        "reason": reason,
        "meta": meta,
    }