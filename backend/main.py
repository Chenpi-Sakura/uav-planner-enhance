from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from schemas import (
    TimeWindowRequest, TimeWindowResponse,
    ReportRequest,
    ProfileRequest, ProfileResponse, ProfileDataPoint,
    PlanRequest, PlanResponse, PlanMultiResponse,
    FeasibilityRequest, FeasibilityResponse, FeasibilityMultiResponse,
    SitePlanRequest, SitePlanResponse, TaskParseRequest, TaskParseResponse,
)
from data_loader import data_loader
from planner import compute_path_turbulence, plan_path_ex, _haversine_km
from route_options import plan_route_options
from feasibility import assess_feasibility, assess_feasibility_multi, to_response_dict
from time_window import assess_time_window
import sites
import assessor
import geo
from config import ROOT_DIR, settings
from timebase import to_data_time_str
import numpy as np
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


def _require_valid_od(request) -> None:
    """起终点不足一个气象格时按「输入不成立」拒掉，而不是交出一条 0 km 的"可飞"结论。

    只卡 HTTP 入口：feasibility / sites / time_window 都会在进程内自己构造
    PlanRequest（逐段复验、候选基点、Top-K 时刻），那条路上的短段是合法的。
    """
    km = _haversine_km(request.start.lat, request.start.lon,
                       request.end.lat, request.end.lon)
    if km < float(settings.MIN_OD_KM):
        raise HTTPException(
            status_code=400,
            detail=(f"起终点直线距离 {km:.1f} km，不足一个气象数据格"
                    f"（约 {settings.MIN_OD_KM:.0f} km），沿途湍流无从判断。"),
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

    每个采样点按【自己的到达时刻】取数（累计里程 / 速度），与规划器的 4D 判定同口径；
    整条线共用起飞时刻那一帧会让折线图与结论对不上。
    """
    loop = asyncio.get_event_loop()

    elevations = await loop.run_in_executor(None, _fetch_elevations, req.waypoints)

    lats = [wp.lat for wp in req.waypoints]
    lons = [wp.lon for wp in req.waypoints]
    # 累计里程（km）与到达时刻：与 planner 的 base_time + dist/speed 同一套算法，
    # 距离口径也必须跟着 planner 走（大圆距离），否则折线图的时刻比规划判定偏早/偏晚。
    cum_km: list[float] = []
    for i, wp in enumerate(req.waypoints):
        if i == 0:
            cum_km.append(0.0)
        else:
            prev = req.waypoints[i - 1]
            cum_km.append(cum_km[-1] + _haversine_km(prev.lat, prev.lon, wp.lat, wp.lon))

    speed = float(req.speed) if req.speed else 60.0
    # 剖面图必须跟随用户选择的日期/起飞时刻；北京时间需换算为数据标签时间(UTC)。
    base_time = np.datetime64(to_data_time_str(f"{req.date}T{req.start_time}"), 'ns')
    arrival = [base_time + np.timedelta64(int(d / speed * 3600), 's') for d in cum_km]
    meteo_batch = await loop.run_in_executor(
        None, data_loader.get_meteorology_at_points, lats, lons, arrival)

    data_points = []
    for i, wp in enumerate(req.waypoints):
        dp = ProfileDataPoint(
            lat=wp.lat,
            lon=wp.lon,
            distance_from_start=round(cum_km[i], 2),
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
    _require_valid_od(request)
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, plan_path_ex, request)

    if not result.found or not result.path:
        # 无安全航线：明确返回 feasible=False，不再用直线兜底，并带上失败原因
        return PlanResponse(path=[], total_distance=0.0, max_tke=0.0, max_edr=0.0,
                            feasible=False, reason=result.reason or 'blocked')

    max_tke, max_edr = await loop.run_in_executor(
        None, compute_path_turbulence, request, result.path)
    return PlanResponse(
        path=result.path,
        total_distance=result.total_distance,
        max_tke=max_tke,
        max_edr=max_edr,
        feasible=True,
    )

@app.post("/api/route/plan-multi", response_model=PlanMultiResponse)
async def plan_route_multi(request: PlanRequest):
    """一次产出「紧急 / 平衡 / 安全」三条候选航线。

    三档只差湍流软惩罚倍率；硬阈值与 NaN 禁区一致。响应里除几何外还带每档的
    里程、绕行比与风险统计，供前端叠图与选主。
    """
    _require_valid_od(request)
    result = await _run_assess(plan_route_options, request)
    return PlanMultiResponse(**result)

@app.post("/api/assess/feasibility", response_model=FeasibilityResponse)
async def assess_route_feasibility(request: FeasibilityRequest):
    """
    功能一 · 飞行路径可行性评估。

    基于 4D 气象数据评估起降点之间是否存在安全可行航线，输出四态结论
    （可飞 / 不可飞 / 超预算 / 未算出）、距离与时间成本、风险等级（全程 +
    分城市段）、阻塞原因、绕行成本分析与建议措施。
    """
    _require_valid_od(request)
    result = await _run_assess(assess_feasibility, request)
    return FeasibilityResponse(**to_response_dict(result))

@app.post("/api/assess/time-window", response_model=TimeWindowResponse)
async def assess_time_window_endpoint(request: TimeWindowRequest):
    """
    功能二 · 飞行时间窗口规划。

    在期望日期内寻找湍流最低且全程安全的时间窗口，输出推荐起飞时刻、
    推荐飞行时段、全程风险等级、湍流强度变化曲线、备用时段与预警提示；
    当日无安全窗口时向前推演若干天，输出各时段风险分析与最佳备选日期。

    时间口径：输入与输出均为北京时间；数据标签基准为 UTC（见 time_window 模块）。
    """
    _require_valid_od(request)
    result = await _run_assess(assess_time_window, request)
    return TimeWindowResponse(**result)


@app.post("/api/assess/feasibility-multi", response_model=FeasibilityMultiResponse)
async def assess_route_feasibility_multi(request: FeasibilityRequest):
    """三档一次评估：紧急 / 平衡 / 安全 各一条航线 + 各自的可行性结论。

    请求体的 `priority` 在这里表示「详情面板要展开哪一档」，不影响另外两档的几何。
    """
    _require_valid_od(request)
    result = await _run_assess(assess_feasibility_multi, request)
    return FeasibilityMultiResponse(**result)

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

@app.get("/api/meta/coverage")
async def get_coverage():
    """气象数据的经纬度覆盖框。

    前端用它把「框外 = 这里没有数据」画出来；否则缺测区和低风险区在屏幕上无法区分。
    数据未加载时返回 available=False，前端跳过该图层（不猜一个框出来）。
    """
    bbox = data_loader.coverage_bbox()
    if bbox is None:
        return {"available": False}
    return {"available": True, **bbox}

@app.get("/api/meta/sites")
async def list_sites():
    """站点表视图（M7 多航点模式的输入数据）+ 机型能力包的公开口径。

    站表是决定航段合法性的输入数据，与气象场同级：随仓库一起版本化，
    每条点位带 `source`（公开出处）与 `geodatum`（GCJ-02 / WGS-84 之分）。
    这两个字段供后端与台账使用，界面上只展示地名与海拔，不播报出处。
    """
    try:
        return sites.public_sites()
    except sites.SitesFileError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/site/parse-task", response_model=TaskParseResponse)
async def parse_site_task(request: TaskParseRequest):
    """把一句话任务（"从昌都把 200 公斤兽药送到米林"）抽成表单字段。

    只做抽取不做规划：机型门限、航程锚点一律不进 prompt，解析结果回填表单后
    **要用户点确认**才发起规划。LLM 不可用时降级到关键词规则，并在 `source` 里如实标出来。
    """
    return TaskParseResponse(**sites.parse_task(request))


@app.post("/api/site/plan", response_model=SitePlanResponse)
async def plan_site_route(request: SitePlanRequest):
    """多航点模式：按机型能力包与场地海拔，给出最少中继落地序列 + 逐段 A* 复验。

    不带 `aircraft` 时一次产出双机对照（油动 / 纯电各一份）。两台是竞争关系，
    所以不做「择优丢弃」：一台被拒另一台照出，差异本身就是结论。
    载重只在厂家公开锚点覆盖的区间内作答，超出一律判「无依据」而非外推。
    """
    _require_valid_od(request)
    result = await _run_assess(sites.plan_site_options, request)
    return SitePlanResponse(**result)


@app.get("/api/weather/overlay")
async def get_weather_overlay(min_lat: float, max_lat: float, min_lon: float, max_lon: float, layer: str = 'tke', date: str = '2022-07-01', start_time: str = '08:00'):
    """
    Generate a transparent heatmap PNG for the given bounding box and time.
    """
    # 前端传入北京时间；数据标签为 UTC，查表前统一换算。
    time_str = to_data_time_str(f"{date}T{start_time}")
    loop = asyncio.get_event_loop()
    image_bytes = await loop.run_in_executor(None, data_loader.get_overlay_image, min_lat, max_lat, min_lon, max_lon, layer, time_str)
    # 同一 layer/date/整点/包围框的图是确定的，允许各级缓存复用；
    # 包围框由前端量化到固定格点，否则每次平移都是新键、缓存全数落空。
    return Response(content=image_bytes, media_type="image/png",
                    headers={"Cache-Control": f"public, max-age={int(settings.OVERLAY_CACHE_SECONDS)}"})

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
