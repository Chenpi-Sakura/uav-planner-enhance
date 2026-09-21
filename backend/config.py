import json
import os
from pathlib import Path
from typing import Dict

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

    # thresholds for path planning (can be updated dynamically if needed, or via API)
    TKE_THRESHOLD_HIGH: float = 1.0 
    EDR_THRESHOLD_HIGH: float = 0.3
    
    GRID_RESOLUTION_DEG: float = 0.045 # Approximate resolution for A* grid (~5km)

    # ---- 风险分级阈值（与 planner 的软/硬约束对齐）----
    TKE_RISK_MODERATE: float = 0.4   # TKE >= 此值 -> 至少中度风险
    TKE_RISK_HIGH: float = 0.7       # TKE >= 此值 -> 高风险
    EDR_RISK_MODERATE: float = 0.1   # EDR >= 此值 -> 至少中度风险
    EDR_RISK_HIGH: float = 0.2       # EDR >= 此值 -> 高风险
    # 中度及以上样本占比超过该比例时，整体风险等级上调一级
    RISK_MODERATE_RATIO_PROMOTE: float = 0.35

    # ---- 绕行成本（时间口径）接受比例，按任务优先级 ----
    DETOUR_RATIO_LIMIT: Dict[str, float] = {
        "urgent": 1.25,   # 紧急：最多接受 +25% 时间
        "normal": 1.5,    # 常规：最多 +50%
        "economy": 2.0,   # 经济：最多 +100%
    }
    DEFAULT_PRIORITY: str = "normal"

    # ---- 阻塞区域分析 ----
    BLOCKAGE_MIN_AREA_KM2: float = 400.0   # 忽略过小的危险连通域
    BLOCKAGE_TOP_N: int = 3                # 最多报告 N 个阻塞区

    # ==================== 功能二：飞行时间窗口规划 ====================
    # 扫描步长（数据实为逐小时 8760 帧；会议口径为 3 小时，可通过该值切换）
    TW_SCAN_STEP_HOURS: int = 1
    TW_FORECAST_DAYS: int = 7          # 当日无窗口时向前推演天数
    TW_SAMPLE_STEP_DEG: float = 0.05   # 沿路径采样步长（与气象网格分辨率对齐）
    TW_MAX_BACKUP_WINDOWS: int = 3     # 最多返回的备用时段数
    TW_CURVE_STEP_MINUTES: int = 30    # 湍流曲线采样步长
    TW_CURVE_PAD_HOURS: int = 1        # 曲线评估域相对飞行时段的外扩小时数
    TW_SLAB_CACHE_SIZE: int = 6        # 按天 slab 的 LRU 缓存条目上限
    TW_MAX_SCAN_SLOTS: int = 48        # 单日最多扫描槽位（防御异常步长）

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
        '时间建议、操作建议、数据口径声明。不要输出 JSON，不要复述输入原文。'
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


# 先载入 .env（若存在），再构造 settings；真实环境变量优先级更高，
# 因此 `set UAV_AI_API_KEY=...` 之类的一次性覆盖仍然生效。
load_env_file()

settings = Settings(**_load_backend_config(), **_load_ai_config())
