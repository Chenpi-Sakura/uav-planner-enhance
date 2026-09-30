import json
import os
from pathlib import Path
from typing import Dict, List

from pydantic import BaseModel

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT_DIR / "config.json"
ENV_FILE = ROOT_DIR / ".env"


def load_env_file(path: Path = ENV_FILE) -> int:
    """把仓库根目录 `.env` 里的键值对载入 os.environ（零依赖实现）。

    约定与常见 dotenv 一致：
      - 每行 `KEY=VALUE`，支持 `#` 注释与空行；
      - `KEY = VALUE` 两侧空白会被去除；
      - 值可用单/双引号包裹（用于含空格或 `#` 的 Key）；
      - **已存在的真实环境变量优先**，`.env` 不覆盖它们，便于 CI/容器注入。

    返回实际写入的键数量；文件不存在或格式异常时静默跳过，
    保证「没有 .env 也能正常启动」这一契约（与 AI 未配置自动降级一致）。
    """
    if not path.exists():
        return 0

    written = 0
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0

    for line in raw_lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            written += 1
    return written

class Settings(BaseModel):
    # Server bind address / port (from root config.json)
    HOST: str = "0.0.0.0"
    PORT: int = 9090

    # Data directory
    DATA_DIR: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
    
    # Files
    U_FILE: str = "tibet_2022_upper_u.nc"
    V_FILE: str = "tibet_2022_upper_v.nc"
    W_FILE: str = "tibet_2022_upper_w.nc"
    TC_FILE: str = "tibet_2022_upper_tc.nc"
    RH_FILE: str = "tibet_2022_upper_rh.nc"
    Q_FILE: str = "tibet_2022_upper_qvapor.nc"

    # thresholds for path planning (can be updated if needed, or via API)
    # ---- 湍流参数化（申报书口径：脉动方差，见 meteorology.py）----
    # 脉动量的窗口宽度：中心滑动 ±6h => N=13。
    # 取整天窗口（N=24）会把一个格点一天压成一个值，功能二的逐小时扫描失去意义，
    # 且日变化本身被当成脉动计入（实测 p50 从 4.3 抬到 6.6）；
    # 取 ±3h（N=7）样本太少，估值系统性偏低（p50 2.5）且更噪。
    TURB_WINDOW_HALF_HOURS: int = 6
    TURB_MIN_WINDOW_SAMPLES: int = 5      # 窗口内有效小时数不足则判为无数据
    TURB_EDR_LENGTH_SCALE_M: float = 2000.0   # 湍流外尺度 L，非飞行器尺度
    TURB_EDR_C_EPS: float = 0.8

    # ---- 风险四档（2026-09-23 按新刻度全年样本重标定，见 docs/backlog.md B5）----
    # 切点由 k 场定标：安全 < 8 ≤ 中风险 < 16 ≤ 高风险 < 26 ≤ 完全没法飞
    # 三区域（拉萨—林芝走廊 / 昌都 / 横断山东缘）全年占比几乎一致：
    #   k>=8 16~28%、k>=16 4~6%、k>=26 0.9~1.5%
    # EDR 阈值不是独立量，等于对侧 TKE 阈值经 ε=0.8·k^1.5/L、EDR=ε^(1/3) 换算
    # （L=2000m 时 8/16/26 -> 0.208/0.295/0.376），改任一边的都必须同步另一边。
    TKE_THRESHOLD_HIGH: float = 26.0
    EDR_THRESHOLD_HIGH: float = 0.376

    GRID_RESOLUTION_DEG: float = 0.045 # Approximate resolution for A* grid (~5km)

    # ---- A* 展开预算（原写死 20000，现随该段直线距离伸缩）----
    # 斜率按 2026-09-26 标定集实测重定（代价与启发式都换成真实公里、节点量化改 6 位之后）：
    #   日喀则→昌都 824 km 三档 8087/9296/10552（12.8 次/km 封顶）、
    #   昌都→拉萨 604 km 三档 11586/21869/40204，最贵那档 **66.6 次/km** 是绑定值。
    # 取 100 = 绑定值加五成余量。只有没收敛的用例会付满预算，收敛的按实际用量付，
    # 所以放宽斜率的代价只体现在「未算出」那一次的等待时长上。
    # 历史值 60 是旧尺度+旧计数口径下标的，两处都变了就必须重标（见 docs/backlog.md B1 追加之三）。
    # 下限沿用旧的 20000：这条改动只允许让长线多算，不许让短线少算。
    # 上限 12 万次 = 单段最长约 1200 km 按 100/km 给满；再长的航线撞到的是上限而不是斜率。
    ASTAR_EXPANSIONS_PER_KM: float = 100.0
    ASTAR_MIN_EXPANSIONS: int = 20000
    ASTAR_MAX_EXPANSIONS: int = 120000

    # ---- A* 墙钟上限（2026-09-26 加，与上面的展开预算取先到者）----
    # 展开预算是确定量，但它换算成等待时长不是：一条 2400 km 的线顶格一次，
    # 主搜索只占 7 s，整次请求却要 52~56 s —— 大头是「未算出」时那二十多次
    # 替代起降点全程重规划。所以只给单次搜索设上限是不够的，整次请求也得有一份余额。
    # 单次搜索 18 s：计时从**开始展开**算起，气象冷读不吃这份预算（见 planner 注释）。
    # 整次请求 30 s：主规划、替代起降点扫描、三档并评共用这一份；赠品（替代点）
    # 余额用完就把手上已找到的照常给出，不能让赠品把那次点击拖长。
    # 两者都到不了的请求，仍由 main.ASSESS_TIMEOUT_S=120 兜底成 504。
    ASTAR_TIME_LIMIT_S: float = 18.0
    ASTAR_REQUEST_TIME_LIMIT_S: float = 30.0

    # ---- 路径拉直（string pulling，2026-09-26）----
    # 8 向网格只能走 0°/45°/90°，浅角度航线的"最短网格线"比两点直线长约 7%：
    # 昌都→日喀则 824 km 那条，网格线 887.2 km / 185 个点，拉直后 824.5 km，
    # 沿程最大 TKE 还从 8.39 降到 6.96。A* 负责找可通行的拓扑，这一遍在拓扑里还原几何。
    # 顶点仍按格距铺密（planner._append_straight）：下游的风险样本与区域分段是按折线顶点取的。
    # 关掉它 = 回到纯网格口径（金标里那些折线），留着它 = 里程更接近真实可飞距离。
    PATH_PULL_ENABLED: bool = True

    # ---- 风险分级阈值（与 planner 的软/硬约束对齐）----
    TKE_RISK_MODERATE: float = 8.0     # TKE >= 此值 -> 至少中度风险
    TKE_RISK_HIGH: float = 16.0        # TKE >= 此值 -> 高风险
    EDR_RISK_MODERATE: float = 0.208   # EDR >= 此值 -> 至少中度风险
    EDR_RISK_HIGH: float = 0.295       # EDR >= 此值 -> 高风险
    # 中度及以上样本占比超过该比例时，整体风险等级上调一级
    RISK_MODERATE_RATIO_PROMOTE: float = 0.35

    # ---- A* 软约束惩罚（单位：等效绕行公里数）----
    # 旧刻度下硬阈值处合计罚约 70（TKE 30 + EDR 40），此处按新刻度保持同一量级：
    # (26-8)*1.7 + (0.376-0.208)*180 ≈ 61
    DETOUR_PENALTY_TKE_COEF: float = 1.7
    DETOUR_PENALTY_EDR_COEF: float = 180.0

    # ---- 任务优先级 -> 软惩罚倍率（乘在上面两个基线值上）----
    # 两项同倍缩放：EDR = 0.0737·√k 是同一个湍流量换了单位，分别调会改风险价格的形状。
    # normal 取 1.0，即与既有验收结果同一口径。
    DETOUR_PENALTY_TIER_SCALE: Dict[str, float] = {
        "urgent": 0.25,    # 紧急：只为逼近硬阈值的湍流绕行
        "normal": 1.0,     # 平衡：基线
        "economy": 4.0,    # 安全：宁可绕远也要躲开中风险以上
    }
    # 一次规划产出的三方案顺序
    PLAN_TIERS: List[str] = ["urgent", "normal", "economy"]

    # ---- 绕行成本（时间口径）接受比例，按任务优先级 ----
    DETOUR_RATIO_LIMIT: Dict[str, float] = {
        "urgent": 1.25,   # 紧急：最多接受 +25% 时间
        "normal": 1.5,    # 平衡：最多 +50%
        "economy": 2.0,   # 安全：最多 +100%
    }
    DEFAULT_PRIORITY: str = "normal"

    # ---- 阻塞区域分析 ----
    BLOCKAGE_MIN_AREA_KM2: float = 400.0   # 忽略过小的危险连通域
    BLOCKAGE_TOP_N: int = 3                # 最多报告 N 个阻塞区

    # ---- 入口几何门限 ----
    # 起终点直线距离不足一个气象格就没有"沿途"可判：0.05° 在纬度向约 5.6 km、
    # 经度向 30°N 约 4.8 km，取 5 km。不拦的话它会以一条 0 km 的"可飞航线"通过全链路。
    MIN_OD_KM: float = 5.0

    # ==================== 功能二：飞行时间窗口规划 ====================
    # 扫描步长（小时）。定为 **1 h = 数据本身的分辨率**，把候选起飞时刻扫到每个整点。
    # 2026-09-24 曾按"数据是 3 小时一次"的口径改成 3，随后核对文件证伪：
    # 六个 tibet_2022_upper_*.nc 全是 8760 帧、帧间隔恒为 1 h，且中间帧不是
    # 3 h 真值的线性内插（残差占该帧量级 34%~67%）。证据与结论见 docs/backlog.md B2b。
    # 若对外展示要按 3 小时归并，改这里即可（单日 8 个槽位），但那是主动放弃分辨率。
    TW_SCAN_STEP_HOURS: int = 1
    TW_FORECAST_DAYS: int = 7          # 当日无窗口时向前推演天数
    TW_SAMPLE_STEP_DEG: float = 0.05   # 沿路径采样步长（与气象网格分辨率对齐）
    TW_MAX_BACKUP_WINDOWS: int = 3     # 最多返回的备用时段数
    # Top-K 两遍：粗筛之后，取「推荐 + 备用时段」里前 K 个候选时刻，各按【自身时刻】
    # 规划一条真实航线再重排推荐（backlog B10）。取 4 = 推荐 1 + 备用 3，
    # 正好覆盖现有全部候选，不会出现「某条备用时段配不到航线」。
    TW_TOP_K_ROUTES: int = 4
    # 时间轴相对飞行时段的外扩小时数：slab 必须盖到最后一个采样点的到达时刻，
    # 否则末段会被最近邻吸附到边界时刻（见 time_window 里的 total_hours 注释）。
    # 名字里的 CURVE 是历史包袱——湍流曲线已下架，这里只服务 slab 取窗。
    TW_CURVE_PAD_HOURS: int = 1
    # slab LRU 的条目上限。6 曾经够用，但剖面改「逐点到达时刻取数」后
    # 一次 get_meteorology_at_points 就要占 6 个键（u/v/w/tc/rh/qvapor），
    # 起终点两次校验需要 12 个 —— 上限 6 等于第二次校验必然把第一次全逐出，
    # 三档评估更是一次都命中不了。提到 12 让同一次请求内的复用真正发生。
    TW_SLAB_CACHE_SIZE: int = 12
    # 同时按字节封顶：功能二 8 天扫描的 u/v/w 每块约 20 MB，只数条数会失控。
    TW_SLAB_CACHE_MAX_BYTES: int = 384 * 1024 * 1024
    # A* 湍流场的缓存条数（一条约 5–13 MB）。三档共用同一条，跨日期/走廊切换留几格。
    TW_GRID_CACHE_SIZE: int = 4
    TW_MAX_SCAN_SLOTS: int = 48        # 单日最多扫描槽位（防御异常步长）

    # ==================== M7：站点自动规划（多航点模式） ====================
    # 站表是**输入数据**不是配置：站点集合一旦决定哪些航段合法，它就和气象场一样
    # 必须固定下来才能复现，所以 sites.json 进 git、放仓库根目录与 config.json 同级。
    # 机型能力包（载重/航程锚点/海拔门限及其出处）在 sites.py 里，不放这里——
    # 那些是带引用的领域数据，改它们等于改结论依据，不该和调参混在同一个文件。
    SITES_FILE: str = "sites.json"
    # 默认载重 200 kg 是**证据的上限**，不是商载上限：V2000CG 最大商载 400 kg，
    # 但厂家只给到 200 kg 档的运营航程；超过公开锚点的载重一律判「无依据」而非外推。
    SITE_DEFAULT_PAYLOAD_KG: float = 200.0
    SITE_LEG_MARGIN_CARGO: float = 0.8     # 段长余度：货运
    SITE_LEG_MARGIN_CREW: float = 0.6      # 段长余度：载人（约束模式，见 sites.py 的载人横幅说明）
    # 建图时把段长上限按绕行系数折掉：A* 交出的线比直线长，逐段复验若超段长上限
    # 就是"物理上飞不完"，与其事后报错不如在建图时留余量。取 1.25 = 紧急档的绕行上限。
    SITE_DETOUR_FACTOR: float = 1.25
    SITE_MAX_LEGS: int = 4                 # 逐段 A* 复验的段数上限，超出的段标 reverified=false
    SITE_SCAN_PAD_KM: float = 150.0        # 走廊外扩：候选站点只取走廊附近的
    SITE_MIN_SPACING_KM: float = 2.0       # 候选最小间距，见 §3.2 端点 round(·,2)≈1.1 km 的量化陷阱
    SITE_MAX_SCAN_CELLS: int = 60000       # 邻域统计的格数硬闸（公网无鉴权，见 §11）
    SITE_TOPK_ALT_PER_LEG: int = 3         # 每跳保留的同层备选站点数（气象换位的搜索宽度）
    # 一次请求只算一台（双机对照已于 2026-09-28 撤销）；`aircraft` 留空时用这台。
    # 取值必须是 sites.py AIRCRAFT_PACKS 里的键，界面默认选中的也是它。
    SITE_DEFAULT_AIRCRAFT: str = "td550"

    # 其他预警阈值（除湍流外的飞行提示）
    WARN_RH_RAIN: float = 80.0     # 相对湿度超过该值提示可能降水（代理指标）
    WARN_RH_ICE: float = 90.0      # 更高阈值提示结冰 / 云中飞行风险
    WARN_TEMP_LOW: float = -20.0   # 500hPa 低温提示阈值（℃）
    WARN_WIND_HIGH: float = 15.0   # 风速提示阈值（m/s）

    # ---- 时区口径 ----
    # 实测结论：NetCDF 时间标签为 UTC（依据温度日变化相位对经度回归，
    # 斜率 -0.0663 h/deg 与理论 -1/15 吻合；相位幅度反推当地 14.3h 成立）。
    # DATA_LABEL_IS_UTC=True 时，把 UI 传入的北京时间换算为数据标签时间。
    DATA_LABEL_IS_UTC: bool = True
    DATA_TZ_OFFSET_HOURS: int = 8   # 北京时间 = UTC + 8

    # ---- 替代起降点建议 ----
    ALT_BASE_RADII_KM: list = [10.0, 20.0, 30.0]   # 候选点采样半径
    ALT_BASE_MAX_RESULTS: int = 3                  # 最多返回 N 个替代方案

    # ==================== 气象覆盖层出图质量 ====================
    # 覆盖层是「后端出一张透明 PNG，前端贴成 imageOverlay」，公网链路要把它
    # 整个穿过服务器出口带宽（实测约 2.5 Mbps，1 MB 图 ≈ 3.3 s），所以体积
    # 就是加载时间。下面每个参数都是一次「清晰度 / 字节」的显式取舍。
    # 配置来源：根目录 config.json 的 overlay 段 > 这里的默认值。
    OVERLAY_DPI: int = 150            # matplotlib 画布 dpi（现网旧值 200）
    OVERLAY_MAX_PX: int = 1600        # 图片长边像素上限，按 bbox 实际宽高比分配
    OVERLAY_COLORS: int = 128         # 调色板量化档位数；0 或 1 = 不量化（RGBA 直出）
    OVERLAY_GRID_POINTS: int = 60     # 数据重采样后的网格点数（等值线细节上限）
    OVERLAY_CONTOUR_LEVELS: int = 50  # 填色色阶数
    OVERLAY_LINE_LEVELS: int = 15     # 黑色等值线条数
    # 响应的 Cache-Control max-age（同一小时同一 bbox 图不变）。
    # 注意这个值同时管着服务器那层：实测 nginx 以回源的 max-age 为准，
    # `proxy_cache_valid 200 7d` 并不会覆盖它，所以它就是整条链路的 TTL。
    OVERLAY_CACHE_SECONDS: int = 86400

    # ==================== AI 辅助评估（功能三） ====================
    # 默认关闭：未配置时自动降级为离线规则模板版，保证演示可用性。
    # 配置来源优先级：环境变量 > 根目录 config.json 的 ai 段 > 这里的默认值。
    AI_ENABLED: bool = False           # 是否启用 LLM（OpenAI 兼容接口）
    AI_BASE_URL: str = ''              # 形如 http://localhost:8000/v1
    AI_MODEL: str = 'gpt-4o-mini'      # 模型名（由服务端决定可用值）
    AI_API_KEY_ENV: str = 'UAV_AI_API_KEY'   # 读取 API Key 的环境变量名
    AI_TIMEOUT_S: float = 60.0         # 单次请求超时（秒）
    AI_MAX_TOKENS: int = 900           # 生成上限，避免报告过长
    AI_TEMPERATURE: float = 0.3        # 较低温度换取口径稳定
    # ---- 思考（CoT）输出控制 ----
    # 推理型模型（MiniMax-M3 / DeepSeek-R1 / QwQ 等）会把「思考过程」一并写进输出，
    # 不做处理时报告正文前面会多出一大段自言自语。这里设两道防线：
    #   1) AI_THINKING：请求侧关闭思考。取值 disabled / adaptive / 空字符串
    #      （空 = 不发送该字段，供不认识该参数的网关使用）。MiniMax-M3 等
    #      OpenAI 兼容网关接受 thinking = {"type": "disabled"}。
    #   2) AI_STRIP_THINKING：响应侧兜底，忽略 delta 里的 reasoning 系列字段，
    #      并从 content 中剥离内联思考标签。默认开启，保证正文只保留报告本身。
    AI_THINKING: str = 'disabled'    # 请求侧思考开关
    AI_STRIP_THINKING: bool = True   # 响应侧思考内容兜底过滤
    # 评估接口超时（秒）：超过则返回 504 并提示稍后重试，避免请求长期挂起。
    # 实测端到端 5-8s（暖读），首次冷读可能更久，故默认留足余量。
    ASSESS_TIMEOUT_S: float = 120.0

    AI_SYSTEM_PROMPT: str = (
        '你是无人机飞行决策助手。只允许引用用户给出的 JSON 中的数值与地名，'
        '禁止编造任何气象事实。不确定时使用「数据显示」措辞。'
        '用简洁的中文分点作答，包含：结论概述、航线地理叙事、气象解读、'
        '时间建议、操作建议。不要输出 JSON，不要复述输入原文。'
        '正文里不得出现 JSON 的字段名或键名（如 blocking、alt_bases、max_tke、'
        'flight_time_h），一律改写成中文说法。'
        '公式与单位写成行内 LaTeX（如 $\\mathrm{m}^{2/3}/\\mathrm{s}$）并连着 $ 一起照抄，'
        '不要改写成 m^(2/3)/s 这种 ASCII 写法。'
        '直接给出报告正文：不要输出思考过程、草稿、自我说明或任何分析旁白。'
        'JSON 中的时间已是北京时间（UTC+8），照抄即可，禁止再做时区换算或标注 UTC。'
    )


def _load_backend_config() -> dict:
    """从仓库根目录 config.json 读取后端配置（host / port 等）。"""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("backend", {})
    except (OSError, ValueError):
        return {}


def _load_ai_config() -> dict:
    """从根目录 config.json 的 `ai` 段与环境变量读取 LLM 配置。

    优先级：环境变量 > config.json > Settings 默认值。
    环境变量名可自定义（`AI_API_KEY_ENV`），便于不同部署环境复用。
    """
    cfg: dict = {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        ai = data.get("ai", {}) or {}
        if "enabled" in ai:
            cfg["AI_ENABLED"] = bool(ai["enabled"])
        if ai.get("base_url"):
            cfg["AI_BASE_URL"] = str(ai["base_url"]).rstrip("/")
        if ai.get("model"):
            cfg["AI_MODEL"] = str(ai["model"])
        if ai.get("api_key_env"):
            cfg["AI_API_KEY_ENV"] = str(ai["api_key_env"])
        if ai.get("timeout_s"):
            cfg["AI_TIMEOUT_S"] = float(ai["timeout_s"])
        if ai.get("max_tokens"):
            cfg["AI_MAX_TOKENS"] = int(ai["max_tokens"])
        if ai.get("temperature") is not None:
            cfg["AI_TEMPERATURE"] = float(ai["temperature"])
        if "thinking" in ai:
            cfg["AI_THINKING"] = str(ai["thinking"] or "").strip().lower()
        if "strip_thinking" in ai:
            cfg["AI_STRIP_THINKING"] = bool(ai["strip_thinking"])
    except (OSError, ValueError, TypeError):
        pass

    # 环境变量覆盖（便于不修改仓库文件即切换模型/网关）
    # 注：.env 已在模块导入时载入 os.environ，因此这里能一并读到。
    if os.environ.get("UAV_AI_BASE_URL"):
        cfg["AI_BASE_URL"] = os.environ["UAV_AI_BASE_URL"].rstrip("/")
    if os.environ.get("UAV_AI_MODEL"):
        cfg["AI_MODEL"] = os.environ["UAV_AI_MODEL"]
    if os.environ.get("UAV_AI_ENABLED"):
        cfg["AI_ENABLED"] = os.environ["UAV_AI_ENABLED"].lower() in ("1", "true", "yes", "on")
    # 数值型参数做容错解析：填错时忽略而非让服务启动失败。
    for env_name, field, cast in (
        ("UAV_AI_TIMEOUT_S", "AI_TIMEOUT_S", float),
        ("UAV_AI_MAX_TOKENS", "AI_MAX_TOKENS", int),
        ("UAV_AI_TEMPERATURE", "AI_TEMPERATURE", float),
    ):
        raw = os.environ.get(env_name)
        if raw:
            try:
                cfg[field] = cast(raw)
            except (TypeError, ValueError):
                pass
    # 文本型开关单独处理：thinking 允许显式置空（= 不发送该字段），
    # 因此用「环境变量是否存在」判断，而不是「值是否为真」。
    if "UAV_AI_THINKING" in os.environ:
        cfg["AI_THINKING"] = os.environ["UAV_AI_THINKING"].strip().lower()
    if os.environ.get("UAV_AI_STRIP_THINKING"):
        cfg["AI_STRIP_THINKING"] = os.environ["UAV_AI_STRIP_THINKING"].lower() not in ("0", "false", "no", "off")
    return cfg


def _load_overlay_config() -> dict:
    """从根目录 config.json 的 `overlay` 段读取覆盖层质量参数。

    只做类型容错：填错或留空时忽略该项、沿用默认值，不让服务启动失败。
    非法取值（如 dpi<=0、colors=2）会被夹到安全范围，避免出图直接报错。
    """
    cfg: dict = {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        ov = data.get("overlay", {}) or {}
    except (OSError, ValueError):
        return cfg

    for key, field, cast in (
        ("dpi", "OVERLAY_DPI", int),
        ("max_px", "OVERLAY_MAX_PX", int),
        ("colors", "OVERLAY_COLORS", int),
        ("grid_points", "OVERLAY_GRID_POINTS", int),
        ("contour_levels", "OVERLAY_CONTOUR_LEVELS", int),
        ("line_levels", "OVERLAY_LINE_LEVELS", int),
        ("cache_seconds", "OVERLAY_CACHE_SECONDS", int),
    ):
        if key in ov and ov[key] is not None:
            try:
                cfg[field] = cast(ov[key])
            except (TypeError, ValueError):
                pass

    if "OVERLAY_DPI" in cfg:
        cfg["OVERLAY_DPI"] = min(max(cfg["OVERLAY_DPI"], 30), 400)
    if "OVERLAY_MAX_PX" in cfg:
        cfg["OVERLAY_MAX_PX"] = min(max(cfg["OVERLAY_MAX_PX"], 200), 4096)
    if "OVERLAY_COLORS" in cfg:
        # 量化位数只有 2 的幂有意义；<2 视为关闭量化
        c = cfg["OVERLAY_COLORS"]
        cfg["OVERLAY_COLORS"] = 0 if c < 2 else min(1 << (c - 1).bit_length(), 256)
    if "OVERLAY_GRID_POINTS" in cfg:
        cfg["OVERLAY_GRID_POINTS"] = min(max(cfg["OVERLAY_GRID_POINTS"], 8), 512)
    if "OVERLAY_CONTOUR_LEVELS" in cfg:
        cfg["OVERLAY_CONTOUR_LEVELS"] = min(max(cfg["OVERLAY_CONTOUR_LEVELS"], 2), 256)
    if "OVERLAY_LINE_LEVELS" in cfg:
        cfg["OVERLAY_LINE_LEVELS"] = max(cfg["OVERLAY_LINE_LEVELS"], 0)
    if "OVERLAY_CACHE_SECONDS" in cfg:
        cfg["OVERLAY_CACHE_SECONDS"] = max(cfg["OVERLAY_CACHE_SECONDS"], 0)
    return cfg


# 先载入 .env（若存在），再构造 settings；真实环境变量优先级更高，
# 因此 `set UAV_AI_API_KEY=...` 之类的一次性覆盖仍然生效。
load_env_file()

settings = Settings(**_load_backend_config(), **_load_ai_config(), **_load_overlay_config())
