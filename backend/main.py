from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from schemas import (
    ProfileRequest, ProfileResponse, ProfileDataPoint,
    PlanRequest, PlanResponse,
    FeasibilityRequest, FeasibilityResponse,
)
from data_loader import data_loader
from planner import plan_path, plan_path_ex
from feasibility import assess_feasibility
import geo
from config import settings
import math
import urllib.request
import json
import asyncio

app = FastAPI(title="UAV Planner Backend")

@app.on_event("startup")
async def startup_event():
    print("\n=== Pre-loading weather data... ===")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, data_loader.load_data)
    print("=== Server ready! ===\n")

# Allow CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _fetch_elevations(waypoints):
    elevations = []
    try:
        chunk_size = 100
        for i in range(0, len(waypoints), chunk_size):
            chunk = waypoints[i:i+chunk_size]
            loc_str = "|".join([f"{wp.lat},{wp.lon}" for wp in chunk])
            url = f"https://api.opentopodata.org/v1/srtm90m?locations={loc_str}"
            api_req = urllib.request.Request(url, headers={'User-Agent': 'UAV-Planner/1.0'})
            with urllib.request.urlopen(api_req, timeout=10) as response:
                api_data = json.loads(response.read().decode())
                for res in api_data.get('results', []):
                    val = res.get('elevation')
                    elevations.append(val if val is not None else 3650.0)
    except Exception as e:
        print(f"Elevation fetch error: {e}")
        elevations = [3650.0] * len(waypoints)
    while len(elevations) < len(waypoints):
        elevations.append(3650.0)
    return elevations

@app.post("/api/weather/profile", response_model=ProfileResponse)
async def get_weather_profile(req: ProfileRequest):
    """
    Get meteorological data along a given route (list of waypoints).
    """
    loop = asyncio.get_event_loop()

    elevations = await loop.run_in_executor(None, _fetch_elevations, req.waypoints)

    total_dist = 0.0
    for i, wp in enumerate(req.waypoints):
        if i > 0:
            prev_wp = req.waypoints[i-1]
            dist_deg = math.hypot(wp.lat - prev_wp.lat, wp.lon - prev_wp.lon)
            total_dist += dist_deg * 111.0

    lats = [wp.lat for wp in req.waypoints]
    lons = [wp.lon for wp in req.waypoints]
    meteo_batch = await loop.run_in_executor(None, data_loader.get_meteorology_at_points, lats, lons)

    data_points = []
    running_dist = 0.0
    for i, wp in enumerate(req.waypoints):
        if i > 0:
            prev_wp = req.waypoints[i-1]
            dist_deg = math.hypot(wp.lat - prev_wp.lat, wp.lon - prev_wp.lon)
            running_dist += dist_deg * 111.0

        dp = ProfileDataPoint(
            lat=wp.lat,
            lon=wp.lon,
            distance_from_start=round(running_dist, 2),
            elevation=elevations[i] if i < len(elevations) else 3650.0,
            u=float(meteo_batch['u'][i]),
            v=float(meteo_batch['v'][i]),
            w=float(meteo_batch['w'][i]),
            tc=float(meteo_batch['tc'][i]),
            rh=float(meteo_batch['rh'][i]),
            qvapor=float(meteo_batch['qvapor'][i]),
            tke=float(meteo_batch['tke'][i]),
            edr=float(meteo_batch['edr'][i])
        )
        data_points.append(dp)

    return ProfileResponse(data=data_points)

@app.post("/api/route/plan", response_model=PlanResponse)
async def plan_route(request: PlanRequest):
    """
    Plan a 4D path from start to end via waypoints using A*.
    Returns the path points, total distance, max TKE, and max EDR along the route.
    """
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, plan_path_ex, request)

    if not result.found or not result.path:
        # 无安全航线：明确返回 feasible=False，不再用直线兜底
        return PlanResponse(path=[], total_distance=0.0, max_tke=0.0, max_edr=0.0, feasible=False)

    path, total_dist, max_tke, max_edr = await loop.run_in_executor(None, plan_path, request)
    return PlanResponse(
        path=path,
        total_distance=total_dist,
        max_tke=max_tke,
        max_edr=max_edr,
        feasible=True,
    )

@app.post("/api/assess/feasibility", response_model=FeasibilityResponse)
async def assess_route_feasibility(request: FeasibilityRequest):
    """
    功能一 · 飞行路径可行性评估。

    基于 4D 气象数据评估起降点之间是否存在安全可行航线，输出
    「可飞 / 不可飞」结论、距离与时间成本、风险等级（全程 + 分城市段）、
    阻塞原因、绕行成本分析与建议措施。
    """
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, assess_feasibility, request)
    return FeasibilityResponse(**{
        "feasible": result.feasible,
        "conclusion": result.conclusion,
        "reason": result.reason,
        "priority": result.priority,
        "detour_limit": result.detour_limit,
        "distance": result.distance,
        "flight_time_h": result.flight_time_h,
        "risk": result.risk,
        "blocking": result.blocking,
        "suggestions": result.suggestions,
        "path": result.path,
        "meta": result.meta,
    })

@app.get("/api/meta/cities")
async def list_cities():
    """返回平台内置的城市/基地清单（供前端分段展示与快速选点）。"""
    return {"cities": geo.CITIES}

@app.get("/api/weather/overlay")
async def get_weather_overlay(min_lat: float, max_lat: float, min_lon: float, max_lon: float, layer: str = 'tke', date: str = '2022-07-01', start_time: str = '08:00'):
    """
    Generate a transparent heatmap PNG for the given bounding box and time.
    """
    time_str = f"{date}T{start_time}:00" if len(start_time) <= 5 else start_time
    loop = asyncio.get_event_loop()
    image_bytes = await loop.run_in_executor(None, data_loader.get_overlay_image, min_lat, max_lat, min_lon, max_lon, layer, time_str)
    return Response(content=image_bytes, media_type="image/png")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=True)