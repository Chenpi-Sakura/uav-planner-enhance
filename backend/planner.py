import math
import heapq
import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field

from data_loader import data_loader
from timebase import to_data_time_str
from config import settings
from schemas import Point, PlanRequest


# A* 自读气象框相对起终点的外扩（度）。功能二注入扫描场时按同一口径取框。
SEGMENT_PAD_DEG = 2.0
# 注入场被探出边界时的段结论原因，只在上层用于判别"该回退自读"，不面向用户。
REASON_FIELD_ESCAPE = 'field_escape'
# 节点坐标量化到几位小数。
#
# 原来取 2 位，而格距是 0.045° —— 29.65 加一步是 29.695，四舍五入到 29.69/29.70
# 交替，格距抖成 0.04/0.05。抖动让「斜着的那一跳」变成一条比格点分解更短的直线，
# 启发式的余量被吃掉 0.15 km，与它要抓的退化形态只差 1.7 倍。6 位足够吸收
# 累加浮点误差（~1e-15），格点恢复严格等距，一致性才谈得上按阈值验收。
_NODE_DECIMALS = 6


@dataclass
class SegmentResult:
    """A* 单段规划结果，携带是否成功与失败原因。"""
    path: List[Point]
    found: bool
    reason: Optional[str] = None      # None | 'blocked' | 'iteration_limit' | 'no_data'
    final_g_score: float = 0.0        # 该段结束时的累计 A* 代价
    final_dist: float = 0.0           # 该段结束时的累计物理距离（km）
    times: List[object] = field(default_factory=list)  # 与 path 一一对应的绝对时刻
    tkes: List[float] = field(default_factory=list)    # 与 path 一一对应的 TKE（规划时判定值）
    edrs: List[float] = field(default_factory=list)    # 与 path 一一对应的 EDR（规划时判定值）
    iterations: int = 0       # 真正研究过的路口数（陈旧堆项不计，见主循环的 settled_g）
    explored: int = 0         # 被 g_score 收录过的不同节点数
    expansion_budget: int = 0  # 本次给的展开预算，iterations 的分母（0 = 没跑到搜索）
    escaped: bool = False             # 注入场覆盖不住这次搜索（仅 field= 时可能为 True）


@dataclass
class PlanResult:
    """整条航线规划结果。"""
    path: List[Point]
    total_distance: float
    total_grid_cost: float
    found: bool
    reason: Optional[str] = None
    times: List[object] = field(default_factory=list)  # 与 path 一一对应的绝对时刻
    tkes: List[float] = field(default_factory=list)    # 与 path 一一对应的 TKE（规划时判定值）
    edrs: List[float] = field(default_factory=list)    # 与 path 一一对应的 EDR（规划时判定值）
    waypoints_reached: List[int] = field(default_factory=list)
    segment_results: List[SegmentResult] = field(default_factory=list)


def _cos_lat(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """两点里纬度绝对值较大者的 cos（28~34°N 之间 0.883→0.829，差约 6%）。"""
    return max(1e-6, math.cos(math.radians(max(abs(a[0]), abs(b[0])))))


def _search_cosk(start_lat: float, end_lat: float) -> float:
    """整段搜索共用的经度折算因子：取「起终点外扩 SEGMENT_PAD_DEG 后的最北/最南纬度」。

    取段内能到达的最北/最南纬度，那里 cos 最小 -> 东西向代价的下界最保守，于是 h 对整段
    平移不变，是一个范数（网格最短距离），一致性可以直接验收：实测 4000 组随机
    (a, b, 一跳) 的最差余量恰好为 0 —— 贴着的直跳上取等，从不为负。

    逐点对算 `_cos_lat(a, b)` 同样没测出违反，别把两者当成对错之分；选固定值的理由是
    平移不变带来的可证性。超出该纬度的节点已落在气象框外（读数被钳到边），不成其为航线。
    """
    lat_bound = max(abs(start_lat), abs(end_lat)) + SEGMENT_PAD_DEG
    return max(1e-6, math.cos(math.radians(lat_bound)))


def heuristic(a: Tuple[float, float], b: Tuple[float, float], cosk: float = None) -> float:
    """无障碍 8 向网格上的最短距离，单位是「纬度方向的等效度」（乘 111 即公里）。

    格子不是正方形：一格是 resolution × resolution·cos(纬度)，30°N 上 5.00 km × 4.32 km。
    经典 octile 的 `max + (√2−1)·min` 假设对角一跳 = √2×直的一跳，在这里对角实际是
    hypot(5.00, 4.32) = 6.61 km 而 octile 估 7.07 km —— 启发式比它替代的那一步还贵，
    一致性一破 A* 就退化成反复 relax 的 label-correcting（实测同一格点被重展开 12 次）。
    这里按「先斜走后拉直」算网格距离，与邻居代价同一把尺子，可采纳且一致。

    `cosk` 应由调用方按整段固定给（见 `_search_cosk`）；缺省回落到逐点对算，
    只供单测和独立调用，搜索里不传。
    """
    res = settings.GRID_RESOLUTION_DEG
    if cosk is None:
        cosk = _cos_lat(a, b)
    nx = abs(b[1] - a[1]) / res          # 东西向格数（Δlon 本身，折算在代价里做）
    ny = abs(b[0] - a[0]) / res          # 南北向格数
    # 等效度单位下：南北一格 res，东西一格 res·cosk，对角一格 res·hypot(1, cosk)
    diag = min(nx, ny)
    return res * (diag * math.hypot(1.0, cosk) + abs(nx - ny) * (1.0 if ny > nx else cosk))


def _tiebreak(a: Tuple[float, float], b: Tuple[float, float],
              start: Tuple[float, float], cosk: float) -> float:
    """离直线越远越大，用于在等代价的路径里偏向"更直的那条"。

    它**不能并进 heuristic 参与 g 的支配判定**：这一项随位置每跳可变，而网格距离启发式
    是贴着真实代价的下界，任何加进去的扰动都会把一致性顶破。所以它只作堆的次级键。
    """
    dx1 = a[0] - b[0]
    dy1 = (a[1] - b[1]) * cosk
    dx2 = start[0] - b[0]
    dy2 = (start[1] - b[1]) * cosk
    return abs(dx1 * dy2 - dx2 * dy1) * 0.01

def get_neighbors(node: Tuple[float, float], resolution: float) -> List[Tuple[float, float]]:
    lat, lon = node
    return [
        (lat + resolution, lon),
        (lat - resolution, lon),
        (lat, lon + resolution),
        (lat, lon - resolution),
        (lat + resolution, lon + resolution),
        (lat - resolution, lon - resolution),
        (lat + resolution, lon - resolution),
        (lat - resolution, lon + resolution),
    ]


def _half_step(axis) -> float:
    """轴上相邻格点间距的一半，作为「仍算覆盖内」的容差。

    A* 的节点量化到 `_NODE_DECIMALS` 位，浮点误差会让恰好落在边界格点上的查询差最后一位；
    放半个格距过去，越界判定就只剩「真的探到切片外」这一种含义。
    """
    a = np.asarray(axis, dtype='float64')
    if a.size < 2:
        return 0.0
    d = np.abs(np.diff(a))
    d = d[d > 0]
    return float(np.median(d)) / 2.0 if d.size else 0.0


class _GridIndex:
    """把「坐标值 -> 网格下标」的查询从每次 np.abs(arr - v).argmin() 换成等价快查。

    与 argmin 等价靠两条写死的规则，不是靠「结果差不多」：
      ① 并列时取最小下标。时间轴用 `2*r > dt` 进位（而非 `>=`），
         所以正落在两整点中间的时刻取的是**更早**的那一小时。
      ② 时间轴只有在严格升序且等距时才走算术定位，一旦不等距就退回
         argmin 原路径；lat/lon 不做任何形状假设，只缓存 argmin 的结果。

    lat/lon 的缓存命中率接近 100%，因为 A* 的节点一律量化到 `_NODE_DECIMALS` 位，
    查询值天然落在 0.01° 的有限格点上。

    strict=True 用于【注入场】（外部给来的有限切片）：越界查询返回 -1 而不是
    钳到边界格点，否则切片外的值会被当成真实读数。是否发生过越界记在 outside 上。
    """

    __slots__ = ("_lats", "_lons", "_times_ns", "_t0", "_t_last", "_dt", "_n",
                 "_uniform", "_strict", "_t_tol", "_lat_lo", "_lat_hi",
                 "_lon_lo", "_lon_hi", "_lat_cache", "_lon_cache", "outside")

    def __init__(self, lats, lons, times_arr, strict: bool = False):
        self._lats = lats
        self._lons = lons
        self._times_ns = np.asarray(times_arr, dtype='datetime64[ns]').astype('int64')
        n = int(self._times_ns.size)
        self._n = n
        self._t0 = int(self._times_ns[0]) if n else 0
        self._t_last = int(self._times_ns[-1]) if n else 0
        self._dt = int(self._times_ns[1] - self._times_ns[0]) if n > 1 else 0
        uniform = n > 1 and self._dt > 0
        if uniform and n > 2:
            uniform = bool(np.all(self._times_ns[1:] - self._times_ns[:-1] == self._dt))
        self._uniform = uniform
        self._strict = strict
        self._t_tol = self._dt / 2 if uniform else 0
        lat_tol = _half_step(lats)
        lon_tol = _half_step(lons)
        self._lat_lo = float(np.min(lats)) - lat_tol
        self._lat_hi = float(np.max(lats)) + lat_tol
        self._lon_lo = float(np.min(lons)) - lon_tol
        self._lon_hi = float(np.max(lons)) + lon_tol
        self._lat_cache = {}
        self._lon_cache = {}
        self.outside = False

    def lat(self, v):
        i = self._lat_cache.get(v)
        if i is None:
            i = int(np.abs(self._lats - v).argmin())
            if self._strict and not (self._lat_lo <= v <= self._lat_hi):
                i = -1
            self._lat_cache[v] = i
        if i < 0:
            self.outside = True
        return i

    def lon(self, v):
        i = self._lon_cache.get(v)
        if i is None:
            i = int(np.abs(self._lons - v).argmin())
            if self._strict and not (self._lon_lo <= v <= self._lon_hi):
                i = -1
            self._lon_cache[v] = i
        if i < 0:
            self.outside = True
        return i

    def time(self, t_ns):
        """t_ns 已是自 epoch 起的纳秒整数；返回与 argmin 一致的下标，越界为 -1。"""
        if self._strict and not (self._t0 - self._t_tol <= t_ns <= self._t_last + self._t_tol):
            self.outside = True
            return -1
        if not self._uniform:
            return int(np.abs(self._times_ns - t_ns).argmin())
        q, r = divmod(t_ns - self._t0, self._dt)
        if 2 * r > self._dt:
            q += 1
        if q < 0:
            return 0
        if q >= self._n:
            return self._n - 1
        return int(q)


def _ns_of(dt64):
    """datetime64 -> 自 epoch 起的纳秒整数（单位换算为精确整数倍，不引入误差）。"""
    return int(np.datetime64(dt64, 'ns').astype('int64'))


def _is_safe_at(node, base_ns, dist_km, speed_kmh,
                tke_grid, edr_grid, lookup,
                tke_thresh, edr_thresh):
    """查询某节点在指定累计距离对应时刻的气象，判断是否满足硬约束。

    返回 (safe, tke, edr)。数据缺失（NaN）视为不安全（与 A* 邻居判定一致）。
    """
    if tke_grid is None or lookup is None:
        return False, float('nan'), float('nan')

    elapsed_hours = dist_km / speed_kmh if speed_kmh else 0.0
    t_ns = base_ns + int(elapsed_hours * 3600) * 1000000000  # base_ns 已是数据标签时间
    idx_t = lookup.time(t_ns)
    idx_lat = lookup.lat(node[0])
    idx_lon = lookup.lon(node[1])
    if idx_t < 0 or idx_lat < 0 or idx_lon < 0:
        # 注入场覆盖不到这里：按缺测处理，上层据 lookup.outside 回退到自读网格
        return False, float('nan'), float('nan')
    tke = tke_grid[idx_t, idx_lat, idx_lon]
    edr = edr_grid[idx_t, idx_lat, idx_lon]

    if math.isnan(tke) or math.isnan(edr):
        return False, tke, edr
    if tke >= tke_thresh or edr >= edr_thresh:
        return False, tke, edr
    return True, tke, edr


def _terminal_hop_is_safe(current, end_node, base_ns, start_dist, speed_kmh,
                          tke_grid, edr_grid, lookup,
                          tke_thresh, edr_thresh, resolution):
    """校验「当前节点 -> 终点」这一末段是否全程安全。

    A* 的收尾是直接把终点接上，并未经过邻居扩展，因此必须显式校验，
    否则会出现「规划成功但沿程风险判定不可飞」的矛盾结论。
    校验方式：按网格分辨率对末段离散取点逐一检查。
    """
    dlat = end_node[0] - current[0]
    dlon = end_node[1] - current[1]
    hop_deg = math.hypot(dlat, dlon)
    steps = max(1, int(math.ceil(hop_deg / resolution)))

    for k in range(1, steps + 1):
        frac = k / steps
        node = (round(current[0] + dlat * frac, 2), round(current[1] + dlon * frac, 2))
        seg_km = start_dist + _deg_to_km(current, node)
        safe, _, _ = _is_safe_at(node, base_ns, seg_km, speed_kmh,
                                 tke_grid, edr_grid, lookup,
                                 tke_thresh, edr_thresh)
        if not safe:
            return False
    return True


def _deg_to_km(a, b):
    """两节点间的物理距离（km），与 A* 中的 phys_dist 口径一致。"""
    lat_mid = math.radians((a[0] + b[0]) / 2.0)
    dx = (b[1] - a[1]) * math.cos(lat_mid)
    dy = b[0] - a[0]
    return math.hypot(dx, dy) * 111.0


def tier_risk_scale(priority) -> float:
    """任务优先级 -> 软惩罚倍率；未知或缺省按「平衡」= 1.0。

    三档只改倍率不改形状：TKE 与 EDR 是同一个湍流量的两种单位，分别调会连带改
    两者之间的相对权重。
    """
    key = (priority or settings.DEFAULT_PRIORITY).lower()
    return float(settings.DETOUR_PENALTY_TIER_SCALE.get(key, 1.0))


def expansion_budget(straight_km: float) -> int:
    """A* 单段的展开次数预算：跟着该段直线距离伸缩。

    写死的上限对长短两条线是两种强度——824 km 的线连搜索框（起终点外接矩形
    各放 SEGMENT_PAD_DEG）都有三万多个格点，20000 次连一遍都扫不完，
    于是「没算完」被当成结论报出去。下限沿用旧值：只许让长线多算。
    """
    budget = int(round(settings.ASTAR_EXPANSIONS_PER_KM * max(0.0, float(straight_km))))
    return max(int(settings.ASTAR_MIN_EXPANSIONS),
               min(int(settings.ASTAR_MAX_EXPANSIONS), budget))


def search_effort_of(plan) -> Optional[Dict[str, int]]:
    """一次规划「真正研究过的路口数 / 展开预算」，用来把「没算完」与「没路」分开。

    整条线按段求和（预算本来就是按段给的）。起点自身被封或没有数据时压根没进搜索，
    预算为 0，此时返回 None —— 报一个「0 / 0」比不报更容易被读错。
    """
    budget = sum(int(s.expansion_budget) for s in plan.segment_results)
    if not budget:
        return None
    return {"expansions": sum(int(s.iterations) for s in plan.segment_results),
            "budget": budget}


def plan_path_segment(start_node, end_node, tke_thresh, edr_thresh, start_time_str,
                      initial_g_score, initial_dist, speed_kmh, risk_scale: float = 1.0,
                      field=None) -> SegmentResult:
    """
    A* path planning algorithm on a 2D grid, with dynamic time progression.

    返回 SegmentResult：found=False 时不再伪装成直线路径，交由上层判定
    「无法飞行」，避免把兜底直线误当成安全航线。

    field 给定时复用外部已加载的 (tke, edr, lats, lons, times) 切片而不自读 NetCDF；
    该切片是有限区域，搜索一旦探到切片外，结论按 escaped 交给上层回退。
    """
    open_set = []
    # 堆项是 (f, 离直线的偏离, 节点)：f 保证弹出序按非减 f（一致性 → 首弹即最优），
    # 次级键只用来在等代价里挑更直的那条，不参与 g 的支配判定
    heapq.heappush(open_set, (0, 0.0, start_node))
    came_from = {}
    
    g_score = {start_node: initial_g_score}
    real_dist = {start_node: initial_dist}
    node_tke = {}
    node_edr = {}
    # 节点 -> 它上一次被展开时的 g_score，用来识别堆里的陈旧副本（见主循环注释）
    settled_g = {}

    resolution = settings.GRID_RESOLUTION_DEG
    # 整段固定的经度折算因子，喂给 heuristic（逐点对算会让 h 不是一致，见 _search_cosk）
    h_cosk = _search_cosk(start_node[0], end_node[0])
    f_score = {start_node: heuristic(start_node, end_node, h_cosk) * 111.0}

    max_iter = expansion_budget(_deg_to_km(start_node, end_node))
    iterations = 0
    
    base_time = np.datetime64(start_time_str)
    
    # Pre-fetch vectorized weather grid for the bounding box
    if field is None:
        pad = SEGMENT_PAD_DEG
        min_lat = min(start_node[0], end_node[0]) - pad
        max_lat = max(start_node[0], end_node[0]) + pad
        min_lon = min(start_node[1], end_node[1]) - pad
        max_lon = max(start_node[1], end_node[1]) + pad
        tke_grid, edr_grid, lats, lons, times_arr = data_loader.get_meteorology_grid(min_lat, max_lat, min_lon, max_lon, date=start_time_str)
    else:
        tke_grid, edr_grid, lats, lons, times_arr = field

    # 日期或区域落在 NetCDF 覆盖之外时，xarray 的 sel 返回的是**空数组**而不是 None，
    # 此前表现为 argmin 打在空序列上抛 ValueError、接口直接 500。这里给出显式结论，
    # 让上层按「数据覆盖不足」解释，而不是把它伪装成「被湍流封死」。
    if tke_grid is not None and (times_arr.size == 0 or lats.size == 0 or lons.size == 0):
        return SegmentResult(
            path=[], found=False, reason='no_data',
            final_g_score=initial_g_score, final_dist=initial_dist,
            times=[], tkes=[], edrs=[], iterations=0, explored=0,
            escaped=field is not None,      # 注入场是空的：换自读网格重新给结论
        )

    # base_time 在整个 A* 过程中恒定，纳秒基准只换算一次
    base_ns = _ns_of(base_time)
    lookup = None if tke_grid is None else _GridIndex(lats, lons, times_arr,
                                                      strict=field is not None)

    def _finish(res: SegmentResult) -> SegmentResult:
        """注入场被探出边界时，这次搜索看到的地图是残缺的，结论不可信。

        标成 escaped 让上层改用自读网格重算 —— 不能把「场不够大」说成「不可飞」。
        """
        if lookup is not None and lookup.outside:
            res.escaped = True
        return res

    # 起点安全校验：起点本身处于高危区时不应给出可行结论
    if tke_grid is not None:
        start_safe, start_tke, start_edr = _is_safe_at(
            start_node, base_ns, initial_dist, speed_kmh,
            tke_grid, edr_grid, lookup, tke_thresh, edr_thresh)
        node_tke[start_node] = start_tke
        node_edr[start_node] = start_edr
        if not start_safe:
            return _finish(SegmentResult(
                path=[], found=False, reason='blocked',
                final_g_score=initial_g_score, final_dist=initial_dist,
                times=[], tkes=[], edrs=[], iterations=0, explored=0,
            ))

    while open_set and iterations < max_iter:
        current = heapq.heappop(open_set)[2]      # 堆项是 (f, 偏离, 节点)

        # 「我来过」标记：一个节点会因 g_score 被改进而在堆里留下多条记录，
        # 旧记录弹出后重新展开的输入与新记录完全一致 —— 邻居的代价、硬约束、
        # 软惩罚都只由 g_score[current] 与 real_dist[current] 推导，而这两者
        # 总在同一条赋值里一起更新，所以 g_score 没变即整次展开是纯空转。
        # 空转不计入预算：iterations 从此是「真正研究过的路口数」。
        # 824 km 的线实测有 43~47% 的弹出属于这一类，跳过等于把预算多买回近一倍。
        if current in settled_g and settled_g[current] == g_score[current]:
            continue

        iterations += 1

        # If we are close enough to the end node
        # 「抵达」按真实距离判，不按 h 判：h 里带经度折算，拿它当半径会让同一格
        # 在东西/南北两个方向上的接受半径不一样。末段安全校验随后把这一步逐格查过。
        if _deg_to_km(current, end_node) < resolution * 1.5 * 111.0:
            # 末段安全校验：终点本身与最后一段若不可飞，则不能算作抵达
            if not _terminal_hop_is_safe(current, end_node, base_ns,
                                         real_dist[current], speed_kmh,
                                         tke_grid, edr_grid, lookup,
                                         tke_thresh, edr_thresh, resolution):
                continue

            # 末段是「当前格点 → 终点」的直线（终点不保证落在格点上），
            # 代价与里程用同一个物理距离，与邻居扩展那一步口径一致
            lat_mid = math.radians((current[0] + end_node[0]) / 2.0)
            dx_phys = (end_node[1] - current[1]) * math.cos(lat_mid)
            dy_phys = end_node[0] - current[0]
            hop_phys_dist = math.hypot(dx_phys, dy_phys) * 111.0

            final_g = g_score[current] + hop_phys_dist
            final_dist = real_dist[current] + hop_phys_dist
            
            # Reconstruct path, 并同步记录每个节点的绝对时刻
            # （时刻由 A* 使用的同一 real_dist 推导，保证后续风险采样与规划判断一致）
            chain = []
            node = current
            chain.append(node)
            while node in came_from:
                node = came_from[node]
                chain.append(node)
            chain.reverse()  # [start_node, ..., current]
            nodes = chain + [end_node]
            
            path = [Point(lat=nd[0], lon=nd[1]) for nd in nodes]
            times = []
            tkes_out = []
            edrs_out = []
            for nd in nodes:
                dist_nd = final_dist if nd == end_node else real_dist.get(nd, initial_dist)
                times.append(base_time + np.timedelta64(int(dist_nd / speed_kmh * 3600), 's'))
                if nd == end_node:
                    _, e_tke, e_edr = _is_safe_at(nd, base_ns, final_dist, speed_kmh,
                                                  tke_grid, edr_grid, lookup,
                                                  tke_thresh, edr_thresh)
                    tkes_out.append(float(e_tke))
                    edrs_out.append(float(e_edr))
                else:
                    tkes_out.append(float(node_tke.get(nd, float('nan'))))
                    edrs_out.append(float(node_edr.get(nd, float('nan'))))
            
            return _finish(SegmentResult(
                path=path,
                found=True,
                final_g_score=final_g,
                final_dist=final_dist,
                times=times,
                tkes=tkes_out,
                edrs=edrs_out,
                iterations=iterations,
                explored=len(g_score),
                expansion_budget=max_iter,
            ))
            
        # 只有真正铺开邻居才算「研究过」。末段校验失败而 continue 的那些节点
        # 没有展开过，留待它被更优路径再次弹出时重判。
        settled_g[current] = g_score[current]
        for neighbor in get_neighbors(current, resolution):
            neighbor = (round(neighbor[0], _NODE_DECIMALS), round(neighbor[1], _NODE_DECIMALS))
            
            # 一跳的代价就是它的真实里程（km）。以前这里用「度空间距离」而推时刻/里程
            # 用带 cos 的物理距离，两套单位并存会让软惩罚的「等效绕行公里」在
            # 东西向被多收约 15%，同一片湍流摆在横竖两个方向上不是一个价。
            lat_mid = math.radians((current[0] + neighbor[0]) / 2.0)
            dx_phys = (neighbor[1] - current[1]) * math.cos(lat_mid)
            dy_phys = neighbor[0] - current[0]
            phys_dist = math.hypot(dx_phys, dy_phys) * 111.0
            
            # Predict the absolute time using the real physical distance traveled
            elapsed_hours = (real_dist[current] + phys_dist) / speed_kmh
            current_ns = base_ns + int(elapsed_hours * 3600) * 1000000000

            # Fast O(1) array lookup（下标定位见 _GridIndex，语义与 argmin 一致）
            tke, edr = float('nan'), float('nan')
            if lookup is not None:
                idx_time = lookup.time(current_ns)
                idx_lat = lookup.lat(neighbor[0])
                idx_lon = lookup.lon(neighbor[1])
                if idx_time < 0 or idx_lat < 0 or idx_lon < 0:
                    continue    # 注入场覆盖不到这里，escaped 已记录，交由上层回退
                tke = tke_grid[idx_time, idx_lat, idx_lon]
                edr = edr_grid[idx_time, idx_lat, idx_lon]
            
            # Check thresholds (Hard constraints and Terrain)
            if math.isnan(tke) or math.isnan(edr):
                continue # Terrain obstacle (NaN means underground)
                
            if tke >= tke_thresh or edr >= edr_thresh:
                continue # Severe turbulence obstacle
                
            # 记录该邻居在到达时刻的气象值（供风险评估复用，保证结论一致）
            node_tke[neighbor] = float(tke)
            node_edr[neighbor] = float(edr)

            # Soft constraints/penalties: 中度以上湍流按超出量加收绕行成本
            penalty = 0.0

            # 拐点即风险分档的「中风险」下沿，罚量与硬阈值处的等效绕行公里数对齐
            if tke >= settings.TKE_RISK_MODERATE:
                penalty += (tke - settings.TKE_RISK_MODERATE) * settings.DETOUR_PENALTY_TKE_COEF * risk_scale

            if edr >= settings.EDR_RISK_MODERATE:
                penalty += (edr - settings.EDR_RISK_MODERATE) * settings.DETOUR_PENALTY_EDR_COEF * risk_scale
                
            tentative_g_score = g_score[current] + phys_dist + penalty

            if neighbor not in g_score or tentative_g_score < g_score[neighbor]:
                came_from[neighbor] = current
                g_score[neighbor] = tentative_g_score
                real_dist[neighbor] = real_dist[current] + phys_dist
                # f 只用「真实里程 + 罚 + 到终点的网格下界」，三者同尺子 → h 一致 →
                # 首次弹出即为最优 g，不会有把同一格点反复改进的重展开
                f_score[neighbor] = tentative_g_score + heuristic(neighbor, end_node, h_cosk) * 111.0
                tie = _tiebreak(neighbor, end_node, start_node, h_cosk)
                heapq.heappush(open_set, (f_score[neighbor], tie, neighbor))
                
    # 搜索失败：区分「被障碍物完全封死」与「达到迭代上限」
    reason = 'iteration_limit' if iterations >= max_iter else 'blocked'
    return _finish(SegmentResult(
        path=[],
        found=False,
        reason=reason,
        final_g_score=initial_g_score,
        final_dist=initial_dist,
        times=[],
        tkes=[],
        edrs=[],
        iterations=iterations,
        explored=len(g_score),
        expansion_budget=max_iter,
    ))


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两点大圆距离（km）。"""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def plan_path_ex(req: PlanRequest, field=None) -> PlanResult:
    """
    Plan a full route connecting start -> waypoints -> end.

    失败时不返回「伪装成航线的直线」，而是明确返回 found=False 及失败原因，
    供可行性评估模块判定。

    field=(tke, edr, lats, lons, times) 时全程复用这份已加载的气象切片，
    不再回盘读取；航线探出切片范围则自动退回自读重算一次。
    """
    result = _plan_seq(req, field)
    if field is not None and result.reason == REASON_FIELD_ESCAPE:
        result = _plan_seq(req, None)
    return result


def _plan_seq(req: PlanRequest, field=None) -> PlanResult:
    """plan_path_ex 的主体：逐段 A*，累计代价与里程供下一段做时间推进。"""
    targets = [req.start] + req.waypoints + [req.end]
    full_path: List[Point] = []
    full_times: List[object] = []
    full_tkes: List[float] = []
    full_edrs: List[float] = []
    segment_results: List[SegmentResult] = []
    waypoints_reached: List[int] = []

    # 对外契约是北京时间，查表前必须换算为数据标签时间（UTC）
    start_time_str = to_data_time_str(req.date + 'T' + req.start_time)
    current_g_score = 0.0
    current_dist = 0.0
    found = True
    fail_reason: Optional[str] = None

    t_thresh = req.tke_threshold if req.tke_threshold is not None else settings.TKE_THRESHOLD_HIGH
    e_thresh = req.edr_threshold if req.edr_threshold is not None else settings.EDR_THRESHOLD_HIGH
    risk_scale = tier_risk_scale(getattr(req, "priority", None))

    for i in range(len(targets) - 1):
        s = targets[i]
        e = targets[i+1]

        s_node = (round(s.lat, 2), round(s.lon, 2))
        e_node = (round(e.lat, 2), round(e.lon, 2))

        seg = plan_path_segment(
            s_node, e_node,
            t_thresh, e_thresh,
            start_time_str, current_g_score, current_dist, req.speed,
            risk_scale=risk_scale, field=field,
        )
        segment_results.append(seg)

        if seg.escaped:
            found = False
            fail_reason = REASON_FIELD_ESCAPE
            break
        if not seg.found:
            found = False
            fail_reason = seg.reason
            break

        # 用本段实际结果推进累计量，供下一段继续做时间推进
        current_g_score = seg.final_g_score
        current_dist = seg.final_dist

        segment_path = seg.path
        segment_times = list(seg.times)
        segment_tkes = list(seg.tkes)
        segment_edrs = list(seg.edrs)
        if i > 0 and len(segment_path) > 0:
            segment_path = segment_path[1:]  # Avoid duplicating the overlapping waypoint
            segment_times = segment_times[1:] if segment_times else segment_times
            segment_tkes = segment_tkes[1:] if segment_tkes else segment_tkes
            segment_edrs = segment_edrs[1:] if segment_edrs else segment_edrs
        full_path.extend(segment_path)
        full_times.extend(segment_times)
        full_tkes.extend(segment_tkes)
        full_edrs.extend(segment_edrs)
        waypoints_reached.append(i + 1)

    total_distance = 0.0
    for k in range(1, len(full_path)):
        total_distance += _haversine_km(
            full_path[k-1].lat, full_path[k-1].lon,
            full_path[k].lat, full_path[k].lon
        )

    return PlanResult(
        path=full_path,
        total_distance=total_distance,
        total_grid_cost=current_g_score,
        found=found,
        reason=fail_reason,
        times=full_times,
        tkes=full_tkes,
        edrs=full_edrs,
        waypoints_reached=waypoints_reached,
        segment_results=segment_results,
    )


def compute_path_turbulence(req: PlanRequest, full_path: List[Point]) -> Tuple[float, float]:
    """
    沿已定好的航线逐点重采样，返回 (max_tke, max_edr)。

    口径与 A* 内层判定一致（同一套 read_turbulence_field），区别在于这里按
    实际到达时刻取帧，因此能覆盖跨天的长航线。
    """
    start_time_str = to_data_time_str(req.date + 'T' + req.start_time)
    base_time = np.datetime64(start_time_str)

    n = len(full_path)
    lats = np.zeros(n)
    lons = np.zeros(n)
    times = np.empty(n, dtype='datetime64[ns]')

    cumulative = 0.0
    for i in range(n):
        lats[i] = full_path[i].lat
        lons[i] = full_path[i].lon
        if i > 0:
            cumulative += _haversine_km(
                full_path[i-1].lat, full_path[i-1].lon,
                full_path[i].lat, full_path[i].lon
            )
        elapsed_hours = cumulative / req.speed
        times[i] = base_time + np.timedelta64(int(elapsed_hours * 3600), 's')

    max_tke = 0.0
    max_edr = 0.0
    try:
        has_data = data_loader._loaded and data_loader.ds_u is not None
    except Exception:
        has_data = False

    if has_data:
        try:
            # 与 A* 内层同一套湍流场：读航线包围盒 + 实际飞行时段的 TKE/EDR，
            # 再逐点取最近格点。旧实现直接对原始风场做 fancy 取点，量的是平均风。
            pad = 0.5
            bbox = (float(lats.min()) - pad, float(lats.max()) + pad,
                    float(lons.min()) - pad, float(lons.max()) + pad)
            t0 = np.datetime64(times[0], 'ns')
            span_h = int(np.ceil((np.datetime64(times[-1], 'ns') - t0)
                                 / np.timedelta64(1, 'h'))) + 1
            tke_grid, edr_grid, glats, glons, gtimes = data_loader.read_turbulence_field(
                t0, span_h, bbox)
            if tke_grid is not None and len(gtimes):
                it = np.abs(gtimes[:, None] - times[None, :]).argmin(axis=0)
                ila = np.abs(glats[:, None] - lats[None, :]).argmin(axis=0)
                ilo = np.abs(glons[:, None] - lons[None, :]).argmin(axis=0)
                tv = tke_grid[it, ila, ilo]
                ev = edr_grid[it, ila, ilo]
                if np.isfinite(tv).any():
                    max_tke = float(np.nanmax(tv))
                    max_edr = float(np.nanmax(ev))
        except Exception:
            max_tke = 0.0
            max_edr = 0.0

    return max_tke, max_edr