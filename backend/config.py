import json
import os
from pathlib import Path
from typing import Dict

from pydantic import BaseModel

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT_DIR / "config.json"

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

    # ---- 替代起降点建议 ----
    ALT_BASE_RADII_KM: list = [10.0, 20.0, 30.0]   # 候选点采样半径
    ALT_BASE_MAX_RESULTS: int = 3                  # 最多返回 N 个替代方案


def _load_backend_config() -> dict:
    """从仓库根目录 config.json 读取后端配置（host / port 等）。"""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("backend", {})
    except (OSError, ValueError):
        return {}


settings = Settings(**_load_backend_config())