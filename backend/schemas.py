from pydantic import BaseModel
from typing import List, Optional

class Point(BaseModel):
    lat: float
    lon: float

class ProfileRequest(BaseModel):
    waypoints: List[Point]

class ProfileDataPoint(BaseModel):
    lat: float
    lon: float
    distance_from_start: float = 0.0  # 方便前端折线图作为X�?    u10: float
    v10: float
    t2: float
    tke: float
    edr: float

class ProfileResponse(BaseModel):
    data: List[ProfileDataPoint]

class PlanRequest(BaseModel):
    start: Point
    end: Point
    # Optional constraints or thresholds
    tke_threshold: Optional[float] = None
    edr_threshold: Optional[float] = None

class PlanResponse(BaseModel):
    path: List[Point]
    total_distance: float
    max_tke: float
    max_edr: float
