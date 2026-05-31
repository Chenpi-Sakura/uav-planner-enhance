import math
import heapq
import numpy as np
import xarray as xr
from typing import List, Tuple
from data_loader import data_loader
from config import settings
from schemas import Point, PlanRequest

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

def plan_path_segment(start_node, end_node, tke_thresh, edr_thresh, start_time_str, initial_g_score, initial_dist, speed_kmh):
    """
    A* path planning algorithm on a 2D grid, with dynamic time progression.
    """
    open_set = []
    heapq.heappush(open_set, (0, start_node))
    came_from = {}
    
    g_score = {start_node: initial_g_score}
    real_dist = {start_node: initial_dist}
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

    while open_set and iterations < max_iter:
        iterations += 1
        current = heapq.heappop(open_set)[1]
        
        # If we are close enough to the end node
        if heuristic(current, end_node) < resolution * 1.5:
            # Reconstruct path
            path = [Point(lat=end_node[0], lon=end_node[1])]
            
            # Use grid distance for A* cost, physical distance for time
            hop_grid_dist = heuristic(current, end_node) * 111.0
            
            lat_mid = math.radians((current[0] + end_node[0]) / 2.0)
            dx_phys = (end_node[1] - current[1]) * math.cos(lat_mid)
            dy_phys = end_node[0] - current[0]
            hop_phys_dist = math.hypot(dx_phys, dy_phys) * 111.0
            
            final_g = g_score[current] + hop_grid_dist
            final_dist = real_dist[current] + hop_phys_dist
            
            while current in came_from:
                path.append(Point(lat=current[0], lon=current[1]))
                current = came_from[current]
            path.append(Point(lat=start_node[0], lon=start_node[1]))
            path.reverse()
            return path, final_g, final_dist
            
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
                
    # If path not found or max iterations reached, return straight line as fallback
    fallback_grid_dist = heuristic(start_node, end_node) * 111.0
    lat_mid = math.radians((start_node[0] + end_node[0]) / 2.0)
    dx_phys = (end_node[1] - start_node[1]) * math.cos(lat_mid)
    dy_phys = end_node[0] - start_node[0]
    fallback_phys_dist = math.hypot(dx_phys, dy_phys) * 111.0
    
    return [Point(lat=start_node[0], lon=start_node[1]), Point(lat=end_node[0], lon=end_node[1])], initial_g_score + fallback_grid_dist, initial_dist + fallback_phys_dist


def plan_path(req: PlanRequest) -> tuple[List[Point], float, float, float]:
    """
    Plan a full route connecting start -> waypoints -> end.
    """
    targets = [req.start] + req.waypoints + [req.end]
    full_path = []
    
    start_time_str = f"{req.date}T{req.start_time}:00" if len(req.start_time) <= 5 else req.start_time
    current_g_score = 0.0
    current_dist = 0.0
    
    for i in range(len(targets) - 1):
        s = targets[i]
        e = targets[i+1]
        
        s_node = (round(s.lat, 2), round(s.lon, 2))
        e_node = (round(e.lat, 2), round(e.lon, 2))
        
        t_thresh = req.tke_threshold if req.tke_threshold is not None else settings.TKE_THRESHOLD_HIGH
        e_thresh = req.edr_threshold if req.edr_threshold is not None else settings.EDR_THRESHOLD_HIGH

        segment_path, current_g_score, current_dist = plan_path_segment(
            s_node, e_node, 
            t_thresh, e_thresh,
            start_time_str, current_g_score, current_dist, req.speed
        )
        
        if i > 0 and len(segment_path) > 0:
            segment_path = segment_path[1:] # Avoid duplicating the overlapping waypoint
            
        full_path.extend(segment_path)

    # Calculate post-planning stats using vectorized batch query
    n = len(full_path)
    total_dist = 0.0
    base_time = np.datetime64(start_time_str)
    lats = np.zeros(n)
    lons = np.zeros(n)
    times = np.empty(n, dtype='datetime64[ns]')
    
    for i in range(n):
        lats[i] = full_path[i].lat
        lons[i] = full_path[i].lon
        if i > 0:
            lat_mid = math.radians((full_path[i].lat + full_path[i-1].lat) / 2.0)
            dx = (full_path[i].lon - full_path[i-1].lon) * math.cos(lat_mid)
            dy = full_path[i].lat - full_path[i-1].lat
            dist = math.hypot(dx, dy) * 111.0
            total_dist += dist
        elapsed_hours = total_dist / req.speed
        times[i] = base_time + np.timedelta64(int(elapsed_hours * 3600), 's')

    try:
        has_data = data_loader._loaded and data_loader.ds_u is not None
    except:
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
    else:
        max_tke = 0.0
        max_edr = 0.0

    return full_path, total_dist, max_tke, max_edr
