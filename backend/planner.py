import math
import heapq
from typing import List, Tuple
from data_loader import data_loader
from config import settings
from schemas import Point

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

def plan_path(start: Point, end: Point, tke_thresh: float, edr_thresh: float) -> List[Point]:
    """
    A* path planning algorithm on a 2D grid.
    Cost incorporates distance, TKE, and EDR.
    """
    start_node = (round(start.lat, 2), round(start.lon, 2))
    end_node = (round(end.lat, 2), round(end.lon, 2))
    
    open_set = []
    heapq.heappush(open_set, (0, start_node))
    came_from = {}
    
    g_score = {start_node: 0}
    f_score = {start_node: heuristic(start_node, end_node)}
    
    resolution = settings.GRID_RESOLUTION_DEG

    # Basic timeout/max_iterations to prevent infinite loops in dummy space
    max_iter = 5000
    iterations = 0

    while open_set and iterations < max_iter:
        iterations += 1
        current = heapq.heappop(open_set)[1]
        
        # If we are close enough to the end node
        if heuristic(current, end_node) < resolution * 1.5:
            # Reconstruct path
            path = [Point(lat=end_node[0], lon=end_node[1])]
            while current in came_from:
                path.append(Point(lat=current[0], lon=current[1]))
                current = came_from[current]
            path.append(Point(lat=start_node[0], lon=start_node[1]))
            path.reverse()
            return path
            
        for neighbor in get_neighbors(current, resolution):
            neighbor = (round(neighbor[0], 2), round(neighbor[1], 2))
            
            # Get meteorology for neighbor
            meteo = data_loader.get_meteorology_at_point(neighbor[0], neighbor[1])
            tke = meteo['tke']
            edr = meteo['edr']
            
            # Check thresholds (Hard constraints)
            if tke > tke_thresh or edr > edr_thresh:
                continue # Obstacle
                
            # Calculate cost (Distance + Penalty)
            dist = heuristic(current, neighbor)
            # Soft constraints/penalties: encourage paths with lower TKE/EDR
            penalty = (tke * 2.0) + (edr * 10.0) 
            tentative_g_score = g_score[current] + dist + penalty
            
            if neighbor not in g_score or tentative_g_score < g_score[neighbor]:
                came_from[neighbor] = current
                g_score[neighbor] = tentative_g_score
                f_score[neighbor] = tentative_g_score + heuristic(neighbor, end_node)
                # Just pushing to heap without checking if it's already there (standard A* simplification)
                heapq.heappush(open_set, (f_score[neighbor], neighbor))
                
    # If path not found or max iterations reached, return straight line as fallback
    return [start, end]
