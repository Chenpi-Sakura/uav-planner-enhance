import math
import heapq
import numpy as np
import xarray as xr
from typing import List, Tuple, Optional
from dataclasses import dataclass, field

from data_loader import data_loader
from config import settings
from schemas import Point, PlanRequest


@dataclass
class SegmentResult:
    """A* 单段规划结果，携带是否成功与失败原因。"""
    path: List[Point]
    found: bool
    reason: Optional[str] = None      # None | 'blocked' | 'iteration_limit'
    final_g_score: float = 0.0        # 该段结束时的累计 A* 代价
    final_dist: float = 0.0           # 该段结束时的累计物理距离（km）
    times: List[object] = field(default_factory=list)  # 与 path 一一对应的绝对时刻
    tkes: List[float] = field(default_factory=list)    # 与 path 一一对应的 TKE（规划时判定值）
    edrs: List[float] = field(default_factory=list)    # 与 path 一一对应的 EDR（规划时判定值）
    iterations: int = 0
    explored: int = 0


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

def _is_safe_at(node, base_time, dist_km, speed_kmh,
                tke_grid, edr_grid, lats, lons, times_arr,
                tke_thresh, edr_thresh):
    """查询某节点在指定累计距离对应时刻的气象，判断是否满足硬约束。

    返回 (safe, tke, edr)。数据缺失（NaN）视为不安全（与 A* 邻居判定一致）。
    """
    if tke_grid is None or lats is None or lons is None or times_arr is None:
        return False, float('nan'), float('nan')

    elapsed_hours = dist_km / speed_kmh if speed_kmh else 0.0
    t = base_time + np.timedelta64(int(elapsed_hours * 3600), 's')
    idx_t = np.abs(times_arr - t).argmin()
    idx_lat = np.abs(lats - node[0]).argmin()
    idx_lon = np.abs(lons - node[1]).argmin()
    tke = tke_grid[idx_t, idx_lat, idx_lon]
    edr = edr_grid[idx_t, idx_lat, idx_lon]

    if math.isnan(tke) or math.isnan(edr):
        return False, tke, edr
    if tke > tke_thresh or edr > edr_thresh:
        return False, tke, edr
    return True, tke, edr


def _terminal_hop_is_safe(current, end_node, base_time, start_dist, speed_kmh,
                          tke_grid, edr_grid, lats, lons, times_arr,
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
        safe, _, _ = _is_safe_at(node, base_time, seg_km, speed_kmh,
                                 tke_grid, edr_grid, lats, lons, times_arr,
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


def plan_path_segment(start_node, end_node, tke_thresh, edr_thresh, start_time_str, initial_g_score, initial_dist, speed_kmh) -> SegmentResult:
    """
    A* path planning algorithm on a 2D grid, with dynamic time progression.

    返回 SegmentResult：found=False 时不再伪装成直线路径，交由上层判定
    「无法飞行」，避免把兜底直线误当成安全航线。
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
    pad = 2.0 # degrees
    min_lat = min(start_node[0], end_node[0]) - pad
    max_lat = max(start_node[0], end_node[0]) + pad
    min_lon = min(start_node[1], end_node[1]) - pad
    max_lon = max(start_node[1], end_node[1]) + pad
    
    tke_grid, edr_grid, lats, lons, times_arr = data_loader.get_meteorology_grid(min_lat, max_lat, min_lon, max_lon, date=start_time_str)

    # 起点安全校验：起点本身处于高危区时不应给出可行结论
    if tke_grid is not None:
        start_safe, start_tke, start_edr = _is_safe_at(
            start_node, base_time, initial_dist, speed_kmh,
            tke_grid, edr_grid, lats, lons, times_arr, tke_thresh, edr_thresh)
        node_tke[start_node] = start_tke
        node_edr[start_node] = start_edr
        if not start_safe:
            return SegmentResult(
                path=[], found=False, reason='blocked',
                final_g_score=initial_g_score, final_dist=initial_dist,
                times=[], tkes=[], edrs=[], iterations=0, explored=0,
            )

    while open_set and iterations < max_iter:
        iterations += 1
        current = heapq.heappop(open_set)[1]
        
        # If we are close enough to the end node
        if heuristic(current, end_node) < resolution * 1.5:
            # 末段安全校验：终点本身与最后一段若不可飞，则不能算作抵达
            if not _terminal_hop_is_safe(current, end_node, base_time,
                                         real_dist[current], speed_kmh,
                                         tke_grid, edr_grid, lats, lons, times_arr,
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
                    _, e_tke, e_edr = _is_safe_at(nd, base_time, final_dist, speed_kmh,
                                                  tke_grid, edr_grid, lats, lons, times_arr,
                                                  tke_thresh, edr_thresh)
                    tkes_out.append(float(e_tke))
                    edrs_out.append(float(e_edr))
                else:
                    tkes_out.append(float(node_tke.get(nd, float('nan'))))
                    edrs_out.append(float(node_edr.get(nd, float('nan'))))
            
            return SegmentResult(
                path=path,
                found=True,
                final_g_score=final_g,
                final_dist=final_dist,
                times=times,
                tkes=tkes_out,
                edrs=edrs_out,
                iterations=iterations,
                explored=len(g_score),
            )
            
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
            current_time = base_time + np.timedelta64(int(elapsed_hours * 3600), 's')
            
            # Fast O(1) vectorized array lookup
            tke, edr = float('nan'), float('nan')
            if tke_grid is not None and lats is not None and lons is not None and times_arr is not None:
                # Find nearest indices
                idx_time = np.abs(times_arr - current_time).argmin()
                idx_lat = np.abs(lats - neighbor[0]).argmin()
                idx_lon = np.abs(lons - neighbor[1]).argmin()
                tke = tke_grid[idx_time, idx_lat, idx_lon]
                edr = edr_grid[idx_time, idx_lat, idx_lon]
            
            # Check thresholds (Hard constraints and Terrain)
            if math.isnan(tke) or math.isnan(edr):
                continue # Terrain obstacle (NaN means underground)
                
            if tke > tke_thresh or edr > edr_thresh:
                continue # Severe turbulence obstacle
                
            # 记录该邻居在到达时刻的气象值（供风险评估复用，保证结论一致）
            node_tke[neighbor] = float(tke)
            node_edr[neighbor] = float(edr)

            # Soft constraints/penalties: severely penalize moderate turbulence
            penalty = 0.0
            
            # TKE >= 0.4 means moderate turbulence
            if tke >= 0.4:
                penalty += (tke - 0.4) * 50.0
                
            # EDR >= 0.1 means moderate turbulence
            if edr >= 0.1:
                penalty += (edr - 0.1) * 200.0
                
            tentative_g_score = g_score[current] + grid_dist + penalty
            
            if neighbor not in g_score or tentative_g_score < g_score[neighbor]:
                came_from[neighbor] = current
                g_score[neighbor] = tentative_g_score
                real_dist[neighbor] = real_dist[current] + phys_dist
                f_score[neighbor] = tentative_g_score + heuristic(neighbor, end_node, start=start_node) * 111.0
                heapq.heappush(open_set, (f_score[neighbor], neighbor))
                
    # 搜索失败：区分「被障碍物完全封死」与「达到迭代上限」
    reason = 'iteration_limit' if iterations >= max_iter else 'blocked'
    return SegmentResult(
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
    )


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两点大圆距离（km）。"""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def plan_path_ex(req: PlanRequest) -> PlanResult:
    """
    Plan a full route connecting start -> waypoints -> end.

    与旧的 plan_path 不同：失败时不再返回「伪装成航线的直线」，而是
    明确返回 found=False 及失败原因，供可行性评估模块判定。
    """
    targets = [req.start] + req.waypoints + [req.end]
    full_path: List[Point] = []
    full_times: List[object] = []
    full_tkes: List[float] = []
    full_edrs: List[float] = []
    segment_results: List[SegmentResult] = []
    waypoints_reached: List[int] = []

    start_time_str = f"{req.date}T{req.start_time}:00" if len(req.start_time) <= 5 else req.start_time
    current_g_score = 0.0
    current_dist = 0.0
    found = True
    fail_reason: Optional[str] = None

    t_thresh = req.tke_threshold if req.tke_threshold is not None else settings.TKE_THRESHOLD_HIGH
    e_thresh = req.edr_threshold if req.edr_threshold is not None else settings.EDR_THRESHOLD_HIGH

    for i in range(len(targets) - 1):
        s = targets[i]
        e = targets[i+1]

        s_node = (round(s.lat, 2), round(s.lon, 2))
        e_node = (round(e.lat, 2), round(e.lon, 2))

        seg = plan_path_segment(
            s_node, e_node,
            t_thresh, e_thresh,
            start_time_str, current_g_score, current_dist, req.speed
        )
        segment_results.append(seg)

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


def plan_path(req: PlanRequest) -> tuple[List[Point], float, float, float]:
    """
    兼容旧调用方的包装：返回 (path, total_distance, max_tke, max_edr)。

    注意：当航线不可达时，path 为空列表（不再用直线兜底）；
    max_tke / max_edr 基于实际路径的沿程气象计算。
    """
    result = plan_path_ex(req)

    if not result.found or not result.path:
        return [], result.total_distance, 0.0, 0.0

    full_path = result.path
    start_time_str = f"{req.date}T{req.start_time}:00" if len(req.start_time) <= 5 else req.start_time
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
            da_lat = xr.DataArray(lats, dims='z')
            da_lon = xr.DataArray(lons, dims='z')
            da_time = xr.DataArray(times, dims='z')

            u_vals = data_loader.ds_u.sel(lat=da_lat, lon=da_lon, method='nearest').u.sel(time=da_time, method='nearest').sel(lev=500).values
            v_vals = data_loader.ds_v.sel(lat=da_lat, lon=da_lon, method='nearest').v.sel(time=da_time, method='nearest').sel(lev=500).values
            w_vals = data_loader.ds_w.sel(lat=da_lat, lon=da_lon, method='nearest').w.sel(time=da_time, method='nearest').sel(lev=500).values

            wind_speed_sq = u_vals**2 + v_vals**2 + w_vals**2
            tke_arr = 0.01 * 0.5 * wind_speed_sq
            edr_arr = 0.5 * (tke_arr ** 1.5) / 100.0

            max_tke = float(np.max(tke_arr))
            max_edr = float(np.max(edr_arr))
        except Exception:
            max_tke = 0.0
            max_edr = 0.0

    return full_path, result.total_distance, max_tke, max_edr