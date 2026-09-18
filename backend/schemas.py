from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Tuple

class Point(BaseModel):
    lat: float
    lon: float

class ProfileRequest(BaseModel):
    waypoints: List[Point]
    date: str = '2022-07-01'
    start_time: str = '08:00'

class ProfileDataPoint(BaseModel):
    lat: float
    lon: float
    distance_from_start: float = 0.0  # 方便前端折线图作为X轴
    elevation: float = 3650.0 # Geographical elevation
    u: float
    v: float
    w: float
    tc: float
    rh: float
    qvapor: float
    tke: float
    edr: float

class ProfileResponse(BaseModel):
    data: List[ProfileDataPoint]

class PlanRequest(BaseModel):
    start: Point
    end: Point
    waypoints: List[Point] = []
    date: str = '2022-07-01'
    start_time: str = '08:00'
    speed: float = 60.0  # UAV speed in km/h
    # Optional constraints or thresholds
    tke_threshold: Optional[float] = None
    edr_threshold: Optional[float] = None

class PlanResponse(BaseModel):
    path: List[Point]
    total_distance: float
    max_tke: float
    max_edr: float
    # 新增：是否规划出真实安全路径（false 时 path 为空，不再用直线兜底）
    feasible: bool = True


# ==================== 功能一：飞行路径可行性评估 ====================

class FeasibilityRequest(BaseModel):
    start: Point
    end: Point
    waypoints: List[Point] = []
    # 任务优先级：urgent 紧急 / normal 常规 / economy 经济，决定绕行成本阈值
    priority: str = "normal"
    date: str = '2022-07-01'
    start_time: str = '08:00'
    speed: float = 60.0
    # 期望飞行窗口（可选，用于报告中说明）
    expected_window: Optional[str] = None
    tke_threshold: Optional[float] = None
    edr_threshold: Optional[float] = None


class TimeCostInfo(BaseModel):
    # applicable=False 表示未规划出航线，这些成本指标无意义（前端显示「不适用」）
    applicable: bool = True
    extra: Optional[float] = None
    limit: float
    limit_ratio: float
    exceeded: bool

class DistanceInfo(BaseModel):
    # applicable=False 表示未规划出航线，距离/时间类指标无意义
    applicable: bool = True
    straight_km: float
    route_km: Optional[float] = None
    detour_ratio: Optional[float] = None
    flight_time_h: Optional[float] = None
    time_cost_h: TimeCostInfo

class RiskStatsModel(BaseModel):
    max_tke: float
    max_edr: float
    mean_tke: float
    mean_edr: float
    moderate_ratio: float
    high_ratio: float
    max_exposure_km: float
    n_samples: int
    worst_index: int

class RiskSampleModel(BaseModel):
    i: int
    lat: Optional[float] = None
    lon: Optional[float] = None
    tke: float
    edr: float
    level: str

class RiskSegmentModel(BaseModel):
    from_name: str
    to_name: str
    level: str
    max_tke: float
    max_edr: float
    distance_km: float
    start_index: int
    end_index: int

class RiskProfileModel(BaseModel):
    overall: str
    stats: RiskStatsModel
    samples: List[RiskSampleModel] = []
    segments: List[RiskSegmentModel] = []

class BlockingRegionModel(BaseModel):
    center: Tuple[float, float]
    radius_km: float
    area_km2: float
    shape: str
    aspect_ratio: float
    max_tke: float
    max_edr: float
    mean_tke: float
    crosses_corridor: bool
    corridor_distance_km: float
    polygon: Optional[List[List[float]]] = None

class BlockingInfo(BaseModel):
    regions: List[BlockingRegionModel] = []
    dominant_shape: Optional[str] = None

class AltBaseSuggestion(BaseModel):
    # role: 'start' 表示替代起点，'end' 表示替代终点
    role: str = "end"
    role_label: str = ""
    end: Point
    level: str
    distance_km: float
    description: str

class SuggestionsModel(BaseModel):
    alt_windows: List[Dict[str, Any]] = []
    alt_bases: List[AltBaseSuggestion] = []
    # 是否真的给出了替代起降点（用于文案与列表保持一致）
    has_alt_bases: bool = False
    text: str = ""

class FeasibilityResponse(BaseModel):
    feasible: bool
    conclusion: str
    reason: Optional[str] = None
    priority: str
    detour_limit: float
    distance: DistanceInfo
    flight_time_h: float
    risk: Optional[RiskProfileModel] = None
    blocking: Optional[BlockingInfo] = None
    suggestions: SuggestionsModel
    path: List[Point] = []
    meta: Dict[str, Any] = {}