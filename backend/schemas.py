from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Tuple

# ==================== 结论四态（功能一 / 功能二共用） ====================
# feasible=False 不等于「飞不了」。verdict 是给前端的机器码（选色、选图标），
# conclusion 是按它渲染的中文。两套 reason 码各自映射到这里的一个枚举：
#   planner:  blocked / no_data / iteration_limit / time_limit
#   feasibility: no_safe_path / data_coverage_insufficient / detour_exceeds_threshold
#                / search_iteration_limit
VERDICT_FEASIBLE = "feasible"
VERDICT_INFEASIBLE = "infeasible"
VERDICT_OVER_BUDGET = "over_budget"
VERDICT_UNDETERMINED = "undetermined"

VERDICT_LABELS: Dict[str, str] = {
    VERDICT_FEASIBLE: "可飞",
    VERDICT_INFEASIBLE: "不可飞",
    VERDICT_OVER_BUDGET: "超预算",
    VERDICT_UNDETERMINED: "未算出",
}

# planner 层还没有「档位预算」概念，因此只会出现 infeasible / undetermined。
# no_data 归 infeasible 是有意的：它性质上确实飞不了，具体原因由 reason 字段区分。
PLANNER_REASON_VERDICT: Dict[str, str] = {
    "blocked": VERDICT_INFEASIBLE,
    "no_data": VERDICT_INFEASIBLE,
    "iteration_limit": VERDICT_UNDETERMINED,
    # 墙钟先到与预算顶格是同一件事的两种说法：都没算完，都不代表没有路
    "time_limit": VERDICT_UNDETERMINED,
}


def verdict_of(feasible: bool, reason: Optional[str], mapping: Dict[str, str]) -> str:
    """把「是否成功 + 失败原因码」折成结论四态。"""
    if feasible:
        return VERDICT_FEASIBLE
    return mapping.get(reason or "", VERDICT_INFEASIBLE)


# ==================== M7 选址的 reason → 四态 ====================
# 三道门各有各的码，不共用「未算出」也不并入「不可飞」：
# 「装不下」和「超出商载上限」是确定的物理否，「这一档没有公开航程数据」是**没依据**，
# 后者必须是 undetermined，否则我们就等于替厂家做了承诺。
SITE_REASON_VERDICT: Dict[str, str] = {
    "payload_exceeds_max": VERDICT_INFEASIBLE,
    # 载重在商载上限内、但超出公开锚点覆盖区间 —— 不外推
    "payload_beyond_published_envelope": VERDICT_UNDETERMINED,
    "site_elevation_exceeds": VERDICT_INFEASIBLE,
    # 海拔证据不可靠（DEM 采样 / 口径冲突）且判定结果贴着门槛
    "site_elevation_unverified": VERDICT_UNDETERMINED,
    "network_unreachable": VERDICT_INFEASIBLE,
    "no_candidate_in_corridor": VERDICT_INFEASIBLE,
    "no_data": VERDICT_INFEASIBLE,
    "outside_coverage": VERDICT_INFEASIBLE,
    # 逐段复验时 A* 自己会说 no_safe_path（走廊被高危湍流封死），那确实是「确定不行」
    "no_safe_path": VERDICT_INFEASIBLE,
    "detour_exceeds_threshold": VERDICT_OVER_BUDGET,
    "search_iteration_limit": VERDICT_OVER_BUDGET,
    "time_limit": VERDICT_UNDETERMINED,
    "iteration_limit": VERDICT_UNDETERMINED,
    "weather_data_missing": VERDICT_UNDETERMINED,
}


class SearchEffort(BaseModel):
    """A* 为这条线真正研究过多少路口、预算给了多少、花了多少秒。

    「未算出」与「不可飞」的分别就在这对数字上：前者是预算用完了还没搜完
    （expansions 顶到 budget），后者是走廊真被封死（远没用完就没前沿了）。
    墙钟先到时也属于前者，此时 expansions 远小于 budget、elapsed_s 顶到上限。
    没跑到搜索（起点自身被封、日期无数据）时整个字段为 null。
    """
    expansions: int
    budget: int
    # 只计展开循环的墙钟，不含气象取数；旧响应里可能没有这个键，故可选
    elapsed_s: Optional[float] = None


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
    # 结论四态，见文件头 VERDICT_* 定义
    verdict: str
    conclusion: str
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
    # 「没算完」与「真没路」的区分依据，见 SearchEffort
    search_effort: Optional[SearchEffort] = None


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
    # 距起点累计里程（km）。由规划器给出的样本才带，缺测时为 None。
    cum_km: Optional[float] = None
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


class TimeWindowRoute(BaseModel):
    """Top-K 候选之一的**真实航线**：按该候选自己的起飞时刻规划，而不是沿用走廊。

    两套风险字段并存是有意的：`scan_*` 是粗排（固定走廊查表）看到的，
    `planned_*` 是该时刻真正该走的那条线看到的。两者可能给出相反结论
    （绕得更远不等于风险更低），前端要并排摊开而不是只报一个。
    """
    takeoff: str                   # 北京时间 ISO，如 2022-07-02T05:00
    window: List[str] = []         # 该候选所属时间窗口 [起, 止] HH:MM
    scan_level: str = ""
    scan_max_tke: Optional[float] = None
    planned_level: str = ""
    feasible: bool = False
    # 结论四态（planner 层只会出现 infeasible / undetermined）：
    # 「未算出」是搜索没收敛，不能和「不可飞」共用一个词
    verdict: str
    conclusion: str
    reason: Optional[str] = None
    path: List[Point] = []
    total_distance: Optional[float] = None
    detour_ratio: Optional[float] = None
    flight_time_h: Optional[float] = None
    max_tke: Optional[float] = None
    max_edr: Optional[float] = None
    moderate_ratio: Optional[float] = None
    high_ratio: Optional[float] = None
    priority: str = "normal"       # 功能二只按「平衡」档规划，切档回功能一
    # iteration_limit 的候选是「没算完」，这对数字让它与「该时刻真没路」分得开
    search_effort: Optional[SearchEffort] = None


class TimeWindowResponse(BaseModel):
    date: str
    has_window: bool
    recommended: Optional[RecommendedWindow] = None
    recommended_final: Optional[RecommendedWindow] = None
    routes: List[TimeWindowRoute] = []
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
    # 结论机器码：feasible / infeasible / over_budget / undetermined（见文件头 VERDICT_*）
    verdict: str
    reason: Optional[str] = None
    priority: str
    detour_limit: float
    distance: DistanceInfo
    flight_time_h: float
    risk: Optional[RiskProfileModel] = None
    blocking: Optional[BlockingInfo] = None
    suggestions: SuggestionsModel
    search_effort: Optional[SearchEffort] = None
    path: List[Point] = []
    meta: Dict[str, Any] = {}


class FeasibilityMultiResponse(BaseModel):
    """三档一次评估的结果：results 顺序与 meta.tiers 一致。"""
    results: List[FeasibilityResponse] = []
    active: str = "normal"
    meta: Dict[str, Any] = {}


# ==================== M7 · 站点自动规划（多航点模式） ====================

class SitePlanRequest(BaseModel):
    """多航点模式的一次请求：给定 OD（或已经手填的 waypoints）+ 机型 + 载重，求中继序列。

    载重是**自变量**：段长上限、可用站点池、需要几处中继都由它派生，
    所以界面允许输入，后端只在厂家公开锚点覆盖的区间内作答（见 SITE_REASON_VERDICT）。
    """
    start: Point
    end: Point
    waypoints: List[Point] = []
    date: str = '2022-07-01'
    start_time: str = '08:00'
    speed: float = 60.0
    # 载重为空时由端点填入 settings.SITE_DEFAULT_PAYLOAD_KG（默认值留给后端，避免两处真相）
    payload_kg: Optional[float] = None
    aircraft: Optional[str] = None        # 为空 = 按 settings.SITE_AIRCRAFT_ORDER 出双机对照
    mission: str = 'cargo'                # cargo | crew
    priority: str = 'normal'              # 逐段复验时沿用哪一档的湍流惩罚倍率
    leg_limit_km: Optional[float] = None  # 仅用于敏感性分析：手工覆盖段长上限
    weather_metrics: bool = True          # Pass 2 的邻域统计开关（关掉可省一次读场）


class SitePlanOption(BaseModel):
    """一台机型在一次请求里的完整答复。"""
    aircraft: str
    label: str
    certified: bool = True
    payload_kg: float = 0.0
    mission: str = 'cargo'
    leg_limit_km: float = 0.0
    margin_k: float = 0.0
    ceiling_m: float = 0.0
    feasible: bool = False
    verdict: str = VERDICT_INFEASIBLE
    conclusion: str = ''
    reason: Optional[str] = None
    message: str = ''
    legs: List[Dict[str, Any]] = []
    relay_count: int = 0
    total_distance: float = 0.0
    total_time_h: Optional[float] = None
    worst_level: str = ''
    pool_sites: int = 0
    usable_sites: int = 0
    diagnostics: Dict[str, Any] = {}
    search_effort: Optional[SearchEffort] = None
    notes: List[str] = []


class SitePlanResponse(BaseModel):
    options: List[SitePlanOption] = []
    meta: Dict[str, Any] = {}


class TaskParseRequest(BaseModel):
    """自然语言任务 → 表单预填。一期是半自动：解析结果不直接触发规划。"""
    text: str
    use_llm: bool = True


class TaskParseResponse(BaseModel):
    fields: Dict[str, Any] = {}
    rule_fields: Dict[str, Any] = {}
    # llm = 模型抽取；rule = 关键词兜底。界面必须显示这个来源，
    # 否则用户会把"规则猜的"当成"AI 理解的"。
    source: str = "rule"
