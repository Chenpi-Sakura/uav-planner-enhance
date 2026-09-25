import math
import heapq
import numpy as np
from typing import List, Tuple, Optional
from dataclasses import dataclass, field

from data_loader import data_loader
from timebase import to_data_time_str
from config import settings
from schemas import Point, PlanRequest


# A* 自读气象框相对起终点的外扩（度）。功能二注入扫描场时按同一口径取框。
SEGMENT_PAD_DEG = 2.0
# 注入场被探出边界时的段结论原因，只在上层用于判别"该回退自读"，不面向用户。
REASON_FIELD_ESCAPE = 'field_escape'


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
    iterations: int = 0
    explored: int = 0
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


def heuristic(a: Tuple[float, float], b: Tuple[float, float], start: Tuple[float, float] = None) -> float:
    # Octile distance for 8-way grid heuristic (in degree space to maintain perfect A* grid properties)
    dx = abs(b[0] - a[0])
    dy = abs(b[1] - a[1])
    h = (max(dx, dy) + (math.sqrt(2) - 1) * min(dx, dy))
    
    # Tie-breaker to prefer paths closer to the straight line from start to end
    if start is not None:
        dx1 = a[0] - b[0]
        dy1 = a[1] - b[1]
        dx2 = start[0] - b[0]
        dy2 = start[1] - b[1]
        cross = abs(dx1*dy2 - dx2*dy1)
        h += cross * 0.01
    return h

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

    A* 的节点量化到 round(x, 2)，浮点误差会让恰好落在边界格点上的查询差最后一位；
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

    lat/lon 的缓存命中率接近 100%，因为 A* 的节点一律量化到 round(x, 2)，
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
    heapq.heappush(open_set, (0, start_node))
    came_from = {}
    
    g_score = {start_node: initial_g_score}
    real_dist = {start_node: initial_dist}
    node_tke = {}
    node_edr = {}
    f_score = {start_node: heuristic(start_node, end_node) * 111.0}
    
    resolution = settings.GRID_RESOLUTION_DEG

    max_iter = 20000
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
        iterations += 1
        current = heapq.heappop(open_set)[1]
        
        # If we are close enough to the end node
        if heuristic(current, end_node) < resolution * 1.5:
            # 末段安全校验：终点本身与最后一段若不可飞，则不能算作抵达
            if not _terminal_hop_is_safe(current, end_node, base_ns,
                                         real_dist[current], speed_kmh,
                                         tke_grid, edr_grid, lookup,
                                         tke_thresh, edr_thresh, resolution):
                continue

            # Use grid distance for A* cost, physical distance for time
            hop_grid_dist = heuristic(current, end_node) * 111.0
            
            lat_mid = math.radians((current[0] + end_node[0]) / 2.0)
            dx_phys = (end_node[1] - current[1]) * math.cos(lat_mid)
            dy_phys = end_node[0] - current[0]
            hop_phys_dist = math.hypot(dx_phys, dy_phys) * 111.0
            
            final_g = g_score[current] + hop_grid_dist
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
            ))
            
        for neighbor in get_neighbors(current, resolution):
            neighbor = (round(neighbor[0], 2), round(neighbor[1], 2))
            
            # Grid distance for A* routing (maintains square grid properties for Octile heuristic)
            grid_dist = math.hypot(neighbor[0] - current[0], neighbor[1] - current[1]) * 111.0
            
            # Physical distance for accurate time prediction and weather fetching
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
                
            tentative_g_score = g_score[current] + grid_dist + penalty
            
            if neighbor not in g_score or tentative_g_score < g_score[neighbor]:
                came_from[neighbor] = current
                g_score[neighbor] = tentative_g_score
                real_dist[neighbor] = real_dist[current] + phys_dist
                f_score[neighbor] = tentative_g_score + heuristic(neighbor, end_node, start=start_node) * 111.0
                heapq.heappush(open_set, (f_score[neighbor], neighbor))
                
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