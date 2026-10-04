import math
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from schemas import (
    TimeWindowRequest, TimeWindowResponse,
    ReportRequest, ChatRequest,
    ProfileRequest, ProfileResponse, ProfileDataPoint,
    PlanRequest, PlanResponse, PlanMultiResponse,
    FeasibilityRequest, FeasibilityResponse, FeasibilityMultiResponse,
    SitePlanRequest, SitePlanResponse, TaskParseRequest, TaskParseResponse,
    IntentRequest, IntentResponse,
    WeatherQueryRequest, WeatherQueryResponse,
    RegionPreviewRequest, RegionPreviewResponse,
    SuggestRequest, SuggestResponse, AgentRequest,
)
from data_loader import data_loader
from planner import compute_path_turbulence, plan_path_ex, _haversine_km
from route_options import plan_route_options
from feasibility import assess_feasibility, assess_feasibility_multi, to_response_dict
from time_window import assess_time_window
import sites
import assessor
import geo
import agent
import intent
import suggest
import tools
import vision
import weather_query
from config import ROOT_DIR, settings
from timebase import to_data_time_str
import numpy as np
import urllib.request
import json
import asyncio

app = FastAPI(title="UAV Planner Backend")


def _build_context(task: str, payload):
    """把任务类型分派到对应的上下文构建器。

    报告与对话两个端点共用这一处：以前各写一遍 if/else，加第三种任务时
    漏掉一个就会让「能生成报告但不能对话」这种不对称出现。
    """
    if task == "time_window":
        return assessor.build_time_window_context(payload)
    if task == "weather":
        return assessor.build_weather_context(payload)
    return assessor.build_feasibility_context(payload)


# 评估用**显式定长**执行器，而不是 loop.run_in_executor(None, ...) 的默认池。
# 原因见 settings.ASSESS_MAX_CONCURRENCY：wait_for 超时后线程仍在后台跑到底，
# 无限派生会拖垮整个进程；定长池把超出的请求挡在队列里，线程数恒定。
_ASSESS_EXECUTOR = ThreadPoolExecutor(
    max_workers=int(settings.ASSESS_MAX_CONCURRENCY),
    thread_name_prefix="assess",
)


async def _run_assess(func, request):
    """在事件循环外执行重计算，并施加超时保护。

    评估接口依赖 NetCDF 冷读，个别情况下 I/O 争用会使单次请求显著变慢；
    这里统一用 ``asyncio.wait_for`` 兜底，超时返回 504 而非让请求无限挂起。

    注意超时只是**客户端不再等待**，排队的那个线程仍会算完 —— 所以并发靠
    执行器长度控制，504 文案也不许承诺「重试会更快」。
    """
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(_ASSESS_EXECUTOR, func, request),
            timeout=settings.ASSESS_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"评估未在 {settings.ASSESS_TIMEOUT_S:.0f} 秒上限内完成"
                   f"（并发上限 {settings.ASSESS_MAX_CONCURRENCY}）。"
                   f"超时的请求仍会在后台算完，请稍后重试。",
        )


def _require_known_aircraft(request) -> None:
    """机型名认不出来就在入口报 400，别让它走到编排层抛 ValueError 变成 500。

    这个端点在公网无鉴权（见 AGENTS.md），任何拼错 `aircraft` 的调用都该看见"可选哪些"，
    而不是一条 traceback。留空的语义是"用默认那台"，与填了错名是两回事。
    """
    key = request.aircraft or settings.SITE_DEFAULT_AIRCRAFT
    if key not in sites.AIRCRAFT_PACKS:
        raise HTTPException(
            status_code=400,
            detail=f"未知机型 {key}（可选：{'、'.join(sites.AIRCRAFT_PACKS)}）")


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
    """向 opentopodata 取 SRTM 高程，按航点下标回填。

    旧写法用 ``append`` 累加，有两个会静默出错的地方：
      1. 该接口对无效坐标可能少返回 ``results`` 条数，append 就让后续点的高程
         与航点**整体错位**，而接口照样返 200，没有任何报错；
      2. 中途抛异常时把已取到的部分全丢掉、统一重填。

    改成预分配 + 按接口回传的 ``location`` 把每个结果对回它原本的航点，
    对不上的宁可留空走兜底值，也不猜位置。
    """
    fallback = settings.SRTM_FALLBACK_ELEV_M
    elevations: list = [None] * len(waypoints)
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
                loc = res.get('location') or {}
                lat, lng = loc.get('lat'), loc.get('lng')
                if lat is None or lng is None:
                    continue
                # 凭经纬找回原航点下标（限本 chunk 范围），保证一一对应。
                # 必须跳过已填的下标：重复航点（A→B→A）时两条结果的经纬完全相同，
                # 不跳过就会都落到第一个 A 上，第二个 A 反而空着走兜底值。
                for idx in range(i, min(i + chunk_size, len(waypoints))):
                    if elevations[idx] is not None:
                        continue
                    wp = waypoints[idx]
                    if abs(wp.lat - lat) < 1e-6 and abs(wp.lon - lng) < 1e-6:
                        val = res.get('elevation')
                        elevations[idx] = fallback if val is None else val
                        break
    except Exception as e:
        # 保留已取到的部分，只让仍为空的位置走兜底
        print(f"Elevation fetch error: {e}")
    return [fallback if v is None else v for v in elevations]

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
            elevation=elevations[i] if i < len(elevations) else settings.SRTM_FALLBACK_ELEV_M,
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
    if not (math.isfinite(max_tke) and math.isfinite(max_edr)):
        # 航线本身搜到了，但沿程湍流统计取不到数。这里**保留航线**：
        # A* 每一格都已按硬阈值判过，丢掉的只是「峰值统计」这一个量。
        # max_tke/max_edr 填 NaN —— pydantic 会把它序列化成 JSON null（不是裸 NaN），
        # 前端据此显示「未算出」，而不是一条零风险曲线。
        # verdict 经 turbulence_unavailable -> undetermined，不会被说成「不可飞」。
        return PlanResponse(path=result.path,
                            total_distance=result.total_distance,
                            max_tke=float('nan'), max_edr=float('nan'),
                            feasible=False, reason='turbulence_unavailable')
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
    ctx = _build_context(request.task, request.payload)

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

@app.post("/api/assess/chat")
async def assess_chat_stream(request: ChatRequest):
    """AI 助手多轮对话（流式）。

    事件类型与 ``/api/assess/report`` 完全一致（meta / delta / error / done），
    前端复用同一套 SSE 解析。可引用的事实只来源于请求体中的结构化结果，
    LLM 未配置或调用失败时降级为复述评估结论，不假装是对话。
    """
    ctx = _build_context(request.task, request.payload)

    history = [{"role": m.role, "content": m.content} for m in request.messages]

    def event_stream():
        for event in assessor.generate_chat_stream(ctx, history):
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


# ==================== 对话层：意图 / 实况 / 胶囊 ====================

@app.post("/api/assess/intent", response_model=IntentResponse)
async def detect_intent_endpoint(request: IntentRequest):
    """一句话 -> 模式 + 实体。对话是全站唯一入口，前端据此联动左侧页签。

    裁判是模型（带 few-shot 的提示词），``sites`` 的规则解析只作离线兜底：
    关键词表在这里必然自相矛盾（「沿途」既是多航点信号又是"问当前结果"信号），
    加词永远补不完。兜底层只做高辨识度的窄判断，拿不准就归 ``chat`` ——
    判错模式会静默切错页签、发错请求，比不联动更糟。
    """
    return IntentResponse(**intent.detect_intent(
        request.text, request.context.date or None))


# 端点在公网无鉴权：一次请求打 200 个点就是一次全 slab 读盘。
# 对话场景实际也就一两个地点，20 足够，超出直接截断而不是报错打断对话。
WEATHER_MAX_POINTS = 20


@app.post("/api/weather/query", response_model=WeatherQueryResponse)
async def query_weather_endpoint(request: WeatherQueryRequest):
    """按地点与时刻取真实的气象实况（TKE / EDR / 风温湿）。

    对话里问「拉萨现在 TKE 是多少」需要真数可引 —— 评估结果里没有原始读数，
    模型硬答就是编。这里真的去气象场取，取到什么给什么。

    缺测一律报「无数据」：``data_loader`` 在文件缺失时会退回 tke/edr=0 的占位值，
    那会让「没数据」显示成「风平浪静」，所以取数前先查数据是否真的加载好了。
    """
    pts = [p.model_dump() for p in request.points[:WEATHER_MAX_POINTS]]
    loop = asyncio.get_event_loop()
    return WeatherQueryResponse(**await loop.run_in_executor(
        None, weather_query.query_weather, pts, request.date, request.time))


@app.post("/api/weather/preview", response_model=RegionPreviewResponse)
async def region_preview(request: RegionPreviewRequest):
    """渲染一块区域的气象图层，供对话里的 Agent **真的看图**。

    复用的是前端贴图用的同一个 ``get_overlay_image``，所以 AI 看到的就是用户
    此刻在地图上看到的那张，不是「另画一套示意图」。

    返回 data URL 而不是二进制：它要同时喂给模型（下一轮走 image_url）和用户
    （对话流里贴出来让人看到 AI 看的是哪张），放一个字段里最省事。
    """
    loop = asyncio.get_event_loop()
    return RegionPreviewResponse(**await loop.run_in_executor(
        None, vision.render_region_image,
        request.lat, request.lon, request.km, request.layer,
        request.date, request.time))


@app.post("/api/assess/suggest", response_model=SuggestResponse)
async def suggest_chips_endpoint(request: SuggestRequest):
    """后续建议胶囊：站在用户视角预测下一步最可能问什么。

    只吃前端送来的结构化结果（与对话同一防幻觉口径），不额外取数。
    端点公网无鉴权，条数与长度在 ``suggest`` 模块内封顶。
    """
    return SuggestResponse(**suggest.suggest_chips(
        task=request.task,
        payload=request.payload,
        last=request.last_assistant,
        recent=request.recent,
        state=request.state,
    ))


@app.post("/api/assess/agent")
async def agent_endpoint(request: AgentRequest):
    """Agent 循环的一步（流式 SSE）。

    事件类型：
      - ``{"type":"tool_calls","calls":[...]}``  这一步要调工具，等前端回结果
      - ``{"type":"delta","text":"..."}``         正文增量
      - ``{"type":"error","message":"..."}``      模型不可用
      - ``{"type":"done"}``                       结束

    一次只走一步是有意的：SSE 是单向的，真正的循环靠前端带 ``tool_results``
    重新 POST 实现（见 agent 模块的说明）。这样模型能看到上一步的执行结果 ——
    「查了没有备降点」它才不会编一个 —— 且每步独立可重试，不需要维持长连接。
    """
    history = [{"role": m.role, "content": m.content} for m in request.messages]

    def event_stream():
        for event in agent.run_agent(request.state, history, request.tool_results,
                                     request.round_index, request.images, request.said):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/site/plan", response_model=SitePlanResponse)
async def plan_site_route(request: SitePlanRequest):
    """多航点模式：按机型能力包给出最少中继落地序列 + 逐段 A* 复验。

    一次请求只算**一台**机型（`aircraft` 留空取 `SITE_DEFAULT_AIRCRAFT`），按该机的巡航速度推时间轴。
    双机对照不做：要比就换一台再点一次，两次请求各自完整复验。
    载重只在厂家公开锚点覆盖的区间内作答，超出一律判「无依据」而非外推。
    """
    _require_valid_od(request)
    _require_known_aircraft(request)
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
