from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from schemas import (
    TimeWindowRequest, TimeWindowResponse,
    ReportRequest,
    ProfileRequest, ProfileResponse, ProfileDataPoint,
    PlanRequest, PlanResponse,
    FeasibilityRequest, FeasibilityResponse,
)
from data_loader import data_loader
from planner import plan_path, plan_path_ex
from feasibility import assess_feasibility
from time_window import assess_time_window
import assessor
import geo
from config import ROOT_DIR, settings
from timebase import to_data_time_str
import math
import urllib.request
import json
import asyncio

app = FastAPI(title="UAV Planner Backend")


async def _run_assess(func, request):
    """在事件循环外执行重计算，并施加超时保护。

    评估接口依赖 NetCDF 冷读，个别情况下 I/O 争用会使单次请求显著变慢；
    这里统一用 ``asyncio.wait_for`` 兜底，超时返回 504 而非让请求无限挂起。
    """
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, func, request),
            timeout=settings.ASSESS_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"评估超时（>{settings.ASSESS_TIMEOUT_S:.0f}s），数据可能正在冷读，请稍后重试。",
        )

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
    # 剖面图必须跟随用户选择的日期/起飞时刻；北京时间需换算为数据标签时间(UTC)。
    data_time = to_data_time_str(f"{req.date}T{req.start_time}")
    meteo_batch = await loop.run_in_executor(
        None, data_loader.get_meteorology_at_points, lats, lons, data_time)

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
    result = await _run_assess(assess_feasibility, request)
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

@app.post("/api/assess/time-window", response_model=TimeWindowResponse)
async def assess_time_window_endpoint(request: TimeWindowRequest):
    """
    功能二 · 飞行时间窗口规划。

    在期望日期内寻找湍流最低且全程安全的时间窗口，输出推荐起飞时刻、
    推荐飞行时段、全程风险等级、湍流强度变化曲线、备用时段与预警提示；
    当日无安全窗口时向前推演若干天，输出各时段风险分析与最佳备选日期。

    时间口径：输入与输出均为北京时间；数据标签基准为 UTC（见 time_window 模块）。
    """
    result = await _run_assess(assess_time_window, request)
    return TimeWindowResponse(**result)


@app.get("/api/assess/report/status")
async def report_status():
    """AI 报告的可用性探测（是否启用 LLM；不返回任何密钥）。"""
    return assessor.report_available()


@app.post("/api/assess/report")
async def assess_report_stream(request: ReportRequest):
    """
    功能三 · AI 辅助评估报告（流式）。

    以 Server-Sent Events 逐段返回报告正文：
      - ``{"type": "meta", "source": "llm"|"rule"}`` 报告来源；
      - ``{"type": "delta", "text": "..."}``    正文增量；
      - ``{"type": "error", "message": "..."}`` LLM 失败并将降级；
      - ``{"type": "done"}``                     结束。

    报告内容只来源于请求体中的结构化结果（不额外取数），LLM 未配置或
    调用失败时自动降级为离线规则模板版，保证演示可用。
    """
    if request.task == "time_window":
        ctx = assessor.build_time_window_context(request.payload)
    else:
        ctx = assessor.build_feasibility_context(request.payload)

    def event_stream():
        for event in assessor.generate_report_stream(ctx):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 关闭反向代理缓冲，保证逐段到达
        },
    )

@app.get("/api/meta/cities")
async def list_cities():
    """返回平台内置的城市/基地清单（供前端分段展示与快速选点）。"""
    return {"cities": geo.CITIES}

@app.get("/api/weather/overlay")
async def get_weather_overlay(min_lat: float, max_lat: float, min_lon: float, max_lon: float, layer: str = 'tke', date: str = '2022-07-01', start_time: str = '08:00'):
    """
    Generate a transparent heatmap PNG for the given bounding box and time.
    """
    # 前端传入北京时间；数据标签为 UTC，查表前统一换算。
    time_str = to_data_time_str(f"{date}T{start_time}")
    loop = asyncio.get_event_loop()
    image_bytes = await loop.run_in_executor(None, data_loader.get_overlay_image, min_lat, max_lat, min_lon, max_lon, layer, time_str)
    return Response(content=image_bytes, media_type="image/png")

# 前端构建产物与 API 共用本端口：公网只需转发这一个端口，页面与接口天然同源。
# 挂载必须在所有 /api 路由之后（Starlette 按注册顺序匹配，根路径挂载会兜走其余请求）。
FRONTEND_DIST = ROOT_DIR / "frontend" / "dist"
if (FRONTEND_DIST / "index.html").is_file():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
else:
    print(f"[WARN] 未找到前端产物 {FRONTEND_DIST}，本进程仅提供 API；请先在 frontend/ 执行 npm run build")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=True)
