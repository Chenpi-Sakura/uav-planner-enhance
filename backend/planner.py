import math
import heapq
import numpy as np
from typing import List, Tuple
from data_loader import data_loader
from config import settings
from schemas import Point, PlanRequest

def heuristic(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    # simple euclidean distance for heuristic
    return math.hypot(b[0] - a[0], b[1] - a[1])

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

def plan_path_segment(start_node, end_node, tke_thresh, edr_thresh, start_time_str, initial_g_score, speed_kmh):
    """
    A* path planning algorithm on a 2D grid, with dynamic time progression.
    """
    open_set = []
    heapq.heappush(open_set, (0, start_node))
    came_from = {}
    
    g_score = {start_node: initial_g_score}
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
            final_g = g_score[current] + heuristic(current, end_node) * 111.0
            while current in came_from:
                path.append(Point(lat=current[0], lon=current[1]))
                current = came_from[current]
            path.append(Point(lat=start_node[0], lon=start_node[1]))
            path.reverse()
            return path, final_g
            
        for neighbor in get_neighbors(current, resolution):
            neighbor = (round(neighbor[0], 2), round(neighbor[1], 2))
            
            dist_km = heuristic(current, neighbor) * 111.0
            
            # Predict the absolute time when UAV reaches this neighbor
            elapsed_hours = (g_score[current] + dist_km) / speed_kmh
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
            import math
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
                
            tentative_g_score = g_score[current] + dist_km + penalty
            
            if neighbor not in g_score or tentative_g_score < g_score[neighbor]:
                came_from[neighbor] = current
                g_score[neighbor] = tentative_g_score
                f_score[neighbor] = tentative_g_score + heuristic(neighbor, end_node) * 111.0
                heapq.heappush(open_set, (f_score[neighbor], neighbor))
                
    # If path not found or max iterations reached, return straight line as fallback
    fallback_g = initial_g_score + heuristic(start_node, end_node) * 111.0
    return [Point(lat=start_node[0], lon=start_node[1]), Point(lat=end_node[0], lon=end_node[1])], fallback_g


def plan_path(req: PlanRequest) -> tuple[List[Point], float, float, float]:
    """
    Plan a full route connecting start -> waypoints -> end.
    """
    targets = [req.start] + req.waypoints + [req.end]
    full_path = []
    
    start_time_str = f"{req.date}T{req.start_time}:00" if len(req.start_time) <= 5 else req.start_time
    current_g_score = 0.0
    
    for i in range(len(targets) - 1):
        s = targets[i]
        e = targets[i+1]
        
        s_node = (round(s.lat, 2), round(s.lon, 2))
        e_node = (round(e.lat, 2), round(e.lon, 2))
        
        t_thresh = req.tke_threshold if req.tke_threshold is not None else settings.TKE_THRESHOLD_HIGH
        e_thresh = req.edr_threshold if req.edr_threshold is not None else settings.EDR_THRESHOLD_HIGH

        segment_path, current_g_score = plan_path_segment(
            s_node, e_node, 
            t_thresh, e_thresh,
            start_time_str, current_g_score, req.speed
        )
        
        if i > 0 and len(segment_path) > 0:
            segment_path = segment_path[1:] # Avoid duplicating the overlapping waypoint
            
        full_path.extend(segment_path)

    # Calculate post-planning stats accurately along the stitched path
    total_dist = 0.0
    max_tke = 0.0
    max_edr = 0.0
    
    base_time = np.datetime64(start_time_str)
    
    for i in range(len(full_path)):
        if i > 0:
            dist = math.hypot(full_path[i].lon - full_path[i-1].lon, full_path[i].lat - full_path[i-1].lat) * 111.0
            total_dist += dist
            
        elapsed_hours = total_dist / req.speed
        current_time = base_time + np.timedelta64(int(elapsed_hours * 3600), 's')
        
        meteo = data_loader.get_meteorology_at_point(full_path[i].lat, full_path[i].lon, date=str(current_time))
        if meteo['tke'] > max_tke: max_tke = meteo['tke']
        if meteo['edr'] > max_edr: max_edr = meteo['edr']

    return full_path, total_dist, max_tke, max_edr
