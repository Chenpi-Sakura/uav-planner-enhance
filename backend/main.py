from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from schemas import ProfileRequest, ProfileResponse, ProfileDataPoint, PlanRequest, PlanResponse
from data_loader import data_loader
from planner import plan_path
from config import settings
import math

app = FastAPI(title="UAV Planner Backend")

# Allow CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/api/weather/profile", response_model=ProfileResponse)
async def get_weather_profile(req: ProfileRequest):
    """
    Get meteorological data along a given route (list of waypoints).
    """
    data_points = []
    total_dist = 0.0
    
    for i, wp in enumerate(req.waypoints):
        meteo = data_loader.get_meteorology_at_point(wp.lat, wp.lon)
        
        if i > 0:
            prev_wp = req.waypoints[i-1]
            # simple euclidean distance, converted roughly to km
            dist_deg = math.hypot(wp.lat - prev_wp.lat, wp.lon - prev_wp.lon)
            total_dist += dist_deg * 111.0
            
        dp = ProfileDataPoint(
            lat=wp.lat,
            lon=wp.lon,
            distance_from_start=round(total_dist, 2),
            u10=meteo['u10'],
            v10=meteo['v10'],
            t2=meteo['t2'],
            tke=meteo['tke'],
            edr=meteo['edr']
        )
        data_points.append(dp)
    
    return ProfileResponse(data=data_points)

@app.post("/api/route/plan", response_model=PlanResponse)
async def plan_uav_route(req: PlanRequest):
    """
    Calculate optimal 2D route avoiding high TKE and EDR.
    """
    tke_th = req.tke_threshold if req.tke_threshold is not None else settings.TKE_THRESHOLD_HIGH
    edr_th = req.edr_threshold if req.edr_threshold is not None else settings.EDR_THRESHOLD_HIGH
    
    path = plan_path(req.start, req.end, tke_th, edr_th)
    
    # Calculate simple stats for the path
    total_dist = 0.0
    max_tke = 0.0
    max_edr = 0.0
    
    for i in range(len(path)):
        meteo = data_loader.get_meteorology_at_point(path[i].lat, path[i].lon)
        if meteo['tke'] > max_tke:
            max_tke = meteo['tke']
        if meteo['edr'] > max_edr:
            max_edr = meteo['edr']
            
        if i > 0:
            # simple euclidean dist, convert to rough km if we multiply by 111
            dist_deg = math.hypot(path[i].lat - path[i-1].lat, path[i].lon - path[i-1].lon)
            total_dist += dist_deg * 111.0 # rough approximation
            
    return PlanResponse(
        path=path,
        total_distance=total_dist,
        max_tke=max_tke,
        max_edr=max_edr
    )

from fastapi.responses import Response

@app.get("/api/weather/overlay")
async def get_weather_overlay(min_lat: float, max_lat: float, min_lon: float, max_lon: float, layer: str = 'tke'):
    """
    Generate a transparent heatmap PNG for the given bounding box.
    """
    image_bytes = data_loader.get_overlay_image(min_lat, max_lat, min_lon, max_lon, layer)
    return Response(content=image_bytes, media_type="image/png")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
