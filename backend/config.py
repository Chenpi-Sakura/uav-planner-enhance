import os
from pydantic import BaseModel

class Settings(BaseModel):
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

settings = Settings()
