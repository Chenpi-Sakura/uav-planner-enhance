from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Tuple

class Point(BaseModel):
    lat: float
    lon: float

class ProfileRequest(BaseModel):
    waypoints: List[Point]
    date: str = '2022-07-01'
    start_time: str = '08:00'
    # 用于把每个采样点的累计里程换算成到达时刻（4D 口径：沿程不是同一瞬间）
    speed: float = 60.0

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
    # 任务优先级：决定 A* 湍流软惩罚倍率（urgent/normal/economy）
    priority: str = "normal"
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
    # 失败原因，供前端分文案出提示：blocked 被高危区封死 / no_data 数据未覆盖
    # / iteration_limit 未搜完（不代表没有路）。成功时为 None。
    reason: Optional[str] = None


# ==================== 三方案航线（紧急 / 平衡 / 安全）====================

class RouteOption(BaseModel):
    priority: str
    label: str
    risk_scale: float
    feasible: bool
    reason: Optional[str] = None
    path: List[Point] = []
    total_distance: float = 0.0
    # 相对直线距离的绕行比；未规划出航线时为 None
    detour_ratio: Optional[float] = None
    max_tke: float = 0.0
    max_edr: float = 0.0
    overall_level: str = ""
    moderate_ratio: float = 0.0
    high_ratio: float = 0.0
    # 沿程是否出现中风险以上湍流：False 时软惩罚没有参与代价，三档必然同一条线
    risk_engaged: bool = False
    n_nodes: int = 0


class PlanMultiResponse(BaseModel):
    routes: List[RouteOption] = []
    meta: Dict[str, Any] = {}


# ==================== 功能一：飞行路径可行性评估 ====================

class FeasibilityRequest(BaseModel):
    start: Point
    end: Point
    waypoints: List[Point] = []
    # 任务档位：urgent 紧急 / normal 平衡 / economy 安全（JSON 键名沿用 economy，只有文案改）
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

# ==================== 功能二：飞行时间窗口规划 ====================

# ==================== 功能三：AI 辅助评估 ====================

class ReportRequest(BaseModel):
    # 报告类型：feasibility（功能一）/ time_window（功能二）
    task: str = 'feasibility'
    # 结构化结果原文；后端只读取其中字段，不再向外部取数（防幻觉）
    payload: Dict[str, Any] = {}


class TimeWindowRequest(BaseModel):
    start: Point
    end: Point
    waypoints: List[Point] = []
    date: str = '2022-07-01'
    start_time: str = '08:00'
    speed: float = 60.0
    # 当日无窗口时向前推演天数（0 表示不推演）
    forecast_days: Optional[int] = None
    # 扫描步长（小时）；默认走 config 的 TW_SCAN_STEP_HOURS
    scan_step_hours: Optional[int] = None
    tke_threshold: Optional[float] = None
    edr_threshold: Optional[float] = None


class TimeWindowSlot(BaseModel):
    time: str                      # 北京时间 HH:MM
    max_tke: Optional[float] = None
    max_edr: Optional[float] = None
    level: str
    safe: bool


class TimeWindowWindow(BaseModel):
    start: str
    end: str
    level: str
    max_tke: float
    max_edr: float


class RecommendedWindow(BaseModel):
    takeoff: str                   # 北京时间 ISO，如 2022-07-01T07:00
    window: List[str]              # [起, 止] 北京时间 HH:MM
    landing: str
    level: str
    max_tke: Optional[float] = None
    max_edr: Optional[float] = None


class TurbulenceCurve(BaseModel):
    times: List[str] = []
    max_tke: List[float] = []
    max_edr: List[float] = []
    mean_tke: List[float] = []


class ForecastDay(BaseModel):
    date: str
    has_window: bool
    best_window: Optional[List[str]] = None
    level: Optional[str] = None
    max_tke: Optional[float] = None
    safe_slot_count: int = 0


class ForecastInfo(BaseModel):
    enabled: bool = False
    days: List[ForecastDay] = []
    first_available_date: Optional[str] = None


class TimeWindowResponse(BaseModel):
    date: str
    has_window: bool
    recommended: Optional[RecommendedWindow] = None
    curve: TurbulenceCurve = TurbulenceCurve()
    backup_windows: List[TimeWindowWindow] = []
    warnings: List[Dict[str, Any]] = []
    slot_analysis: List[TimeWindowSlot] = []
    forecast: ForecastInfo = ForecastInfo()
    reason: Optional[str] = None
    meta: Dict[str, Any] = {}


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


class FeasibilityMultiResponse(BaseModel):
    """三档一次评估的结果：results 顺序与 meta.tiers 一致。"""
    results: List[FeasibilityResponse] = []
    active: str = "normal"
    meta: Dict[str, Any] = {}
