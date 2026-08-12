import json
import os
from pathlib import Path

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


def _load_backend_config() -> dict:
    """从仓库根目录 config.json 读取后端配置（host / port 等）。"""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("backend", {})
    except (OSError, ValueError):
        return {}


settings = Settings(**_load_backend_config())
