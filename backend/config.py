import os
from pydantic import BaseModel

class Settings(BaseModel):
    # Data directory
    DATA_DIR: str = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
    
    # Files
    U10_FILE: str = "tibet_2022_surface_u10.nc"
    V10_FILE: str = "tibet_2022_surface_v10.nc"
    T2_FILE: str = "tibet_2022_surface_t2.nc"

    # thresholds for path planning (can be updated dynamically if needed, or via API)
    TKE_THRESHOLD_HIGH: float = 5.0 # Example value
    EDR_THRESHOLD_HIGH: float = 0.5 # Example value
    
    # Grid search parameters
    GRID_RESOLUTION_DEG: float = 0.1 # Approximate resolution for A* grid

settings = Settings()
