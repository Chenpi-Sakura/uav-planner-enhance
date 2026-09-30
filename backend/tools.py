"""工具注册表：把「人能在界面上做的操作」变成模型能调用的工具。

分两类，区别在**谁执行**：

* ``layer="ui"``      在浏览器里执行 —— 它们改的是前端的 store 与地图，
  后端碰不到（例如开关叠加层、地图定位、增删途经点）。
* ``layer="compute"`` 由后端自己执行 —— 需要真的读气象场算一遍
  （规划航线、多航点站点、扫时间窗口、查实况、检索站表）。

为什么要有这层而不是继续用 intent + if/else：意图分类只能把一句话归到一个
固定分支，而真实需求是组合的（「打开 TKE 图层，定位到曲松县，看看能不能备降」）。
工具把「会做什么」从路由表里拆出来，模型按需挑着调，没调过的事它就说不出来。

**工具名与参数是模型可见的契约**，前端 ``agentTools.ts`` 按同一份名字实现执行，
两边必须同步改。`args` 里的每个字段都在 ``validate_args`` 里做过取值域校验：
端点在公网无鉴权，模型说什么就往 store 里塞是不行的。
"""

from typing import Any, Dict, List, Optional

import re

# 叠加层可选值：与前端 store.overlays 里存的一致（见 MapContainer 的 layers）
OVERLAY_KEYS = {
    "tke": "TKE（湍动能）",
    "edr": "EDR（湍动耗散率）",
    "wind": "综合风速",
    "temp": "温度",
    "rh": "相对湿度",
    "q": "水汽混合比",
}
OVERLAY_ALIASES = {
    "动能": "tke", "湍动能": "tke", "湍流动能": "tke", "扰动": "tke",
    "耗散": "edr", "湍动耗散率": "edr",
    "风": "wind", "风速": "wind", "风场": "wind",
    "气温": "temp", "温度": "temp",
    "湿度": "rh", "相对湿度": "rh",
    "水汽": "q", "水汽混合比": "q",
}

TABS = {
    "plan": "路径规划", "timeWindow": "时间窗口", "site": "多航点",
}
TAB_ALIASES = {
    "规划": "plan", "路径规划": "plan", "可行": "plan",
    "时间": "timeWindow", "时间窗口": "timeWindow", "窗口": "timeWindow",
    "多航点": "site", "站点": "site", "物流": "site",
}

PRIORITIES = {
    "urgent": "紧急", "normal": "平衡", "economy": "安全",
}
PRIORITY_ALIASES = {
    "紧急": "urgent", "快": "urgent", "最快": "urgent",
    "平衡": "normal", "适中": "normal", "折中": "normal", "默认": "normal",
    "安全": "economy", "稳妥": "economy", "保守": "economy", "最稳": "economy",
}

MISSIONS = {"cargo": "运货", "crew": "载人"}
MISSION_ALIASES = {"载人": "crew", "送人": "crew", "客运": "crew",
                    "货运": "cargo", "运货": "cargo", "送货": "cargo"}

SIM_ACTIONS = {
    "play": "播放推演", "pause": "暂停推演", "toggle": "播放/暂停",
    "step": "前进一帧", "reset": "重置推演", "stop": "停止推演",
}
SIM_ALIASES = {
    "播放": "play", "开始": "play", "继续": "play", "走": "play",
    "暂停": "pause", "停": "pause",
    "下一步": "step", "前进": "step",
    "重置": "reset", "归零": "reset", "回起点": "reset",
    "停止": "stop", "结束": "stop",
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^([01]?\d|2[0-3])[:：]\d{1,2}$")


# ---------------------------------------------------------------- 工具定义

def _tool(name: str, desc: str, layer: str, props: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {
        "name": name,
        "description": desc,
        "layer": layer,
        "params": {"type": "object", "properties": props, "required": required},
    }


_P = {
    "layer": {"type": "string", "enum": sorted(OVERLAY_KEYS), "description": "图层键"},
    "on": {"type": "boolean", "description": "true 开启，false 关闭"},
    "name": {"type": "string", "description": "地名或站点名（照抄站表，不要自己造）"},
    "lat": {"type": "number", "description": "纬度"},
    "lon": {"type": "number", "description": "经度"},
    "zoom": {"type": "integer", "description": "缩放层级，6~14"},
    "index": {"type": "integer", "description": "序号，从 1 开始；0 表示起点，-1 表示终点"},
    "query": {"type": "string", "description": "检索关键词，如「备降」「机场」"},
    "tab": {"type": "string", "enum": sorted(TABS), "description": "页签"},
    "date": {"type": "string", "description": "YYYY-MM-DD"},
    "time": {"type": "string", "description": "HH:MM"},
    "priority": {"type": "string", "enum": sorted(PRIORITIES)},
    "payload_kg": {"type": "number", "description": "载重，公斤"},
    "aircraft": {"type": "string", "enum": ["td550", "v2000cg"]},
    "mission": {"type": "string", "enum": sorted(MISSIONS)},
    "speed": {"type": "number", "description": "巡航速度 km/h"},
    "action": {"type": "string", "enum": sorted(SIM_ACTIONS)},
    "forecast_days": {"type": "integer", "description": "推演天数 0~15"},
    "sites": {"type": "array", "items": {"type": "string"}, "description": "地点名列表"},
    "km": {"type": "number", "description": "周边半径，公里"},
}

TOOLS: List[Dict[str, Any]] = [
    # ---- 界面操作（浏览器执行）----
    _tool("toggle_overlay", "开启/关闭气象叠加层（TKE、EDR、风速、温度、湿度、水汽）。"
           "用户想看某个气象要素时必须先开对应图层，否则他在地图上看不到。",
          "ui", {k: _P[k] for k in ("layer", "on")}, ["layer", "on"]),
    _tool("focus_region", "把地图视野移到某个地点并设定缩放。用户要看某个位置时用它。",
          "ui", {k: _P[k] for k in ("name", "lat", "lon", "zoom", "km")}, []),
    _tool("add_waypoint", "在航线中插入一个途经点。索引从 1 开始，缺省追加到末尾。",
          "ui", {k: _P[k] for k in ("name", "index")}, []),
    _tool("remove_waypoint", "删除一个途经点。索引从 1 开始。",
          "ui", {"index": _P["index"]}, ["index"]),
    _tool("update_waypoint", "把某个途经点改成别的地点。",
          "ui", {k: _P[k] for k in ("index", "name")}, ["index"]),
    _tool("set_endpoints", "设置起点或终点。index 0 = 起点，-1 = 终点。",
          "ui", {k: _P[k] for k in ("index", "name", "lat", "lon")}, ["index"]),
    _tool("switch_tab", "切换左侧页签（路径规划 / 时间窗口 / 多航点）。",
          "ui", {"tab": _P["tab"]}, ["tab"]),
    _tool("set_time", "修改日期或起飞时刻。",
          "ui", {k: _P[k] for k in ("date", "time")}, []),
    _tool("set_priority", "切换方案档位：urgent 紧急 / normal 平衡 / economy 安全。"
           "**三档是在同一次规划里全部算出来的，所以切档不需要重跑** —— "
           "只调这个工具即可，别为了切档再去调 run_plan。",
          "ui", {"priority": _P["priority"]}, ["priority"]),
    _tool("set_payload_kg", "设置载重（公斤）。", "ui", {"payload_kg": _P["payload_kg"]}, ["payload_kg"]),
    _tool("set_aircraft", "切换机型：td550 油动 / v2000cg 纯电。", "ui", {"aircraft": _P["aircraft"]}, ["aircraft"]),
    _tool("set_mission", "切换任务类型：cargo 运货 / crew 载人。", "ui", {"mission": _P["mission"]}, ["mission"]),
    _tool("set_speed", "设置巡航速度（km/h）。", "ui", {"speed": _P["speed"]}, ["speed"]),
    _tool("set_forecast_days", "设置时间窗口的推演天数（0 = 只看当天）。",
          "ui", {"forecast_days": _P["forecast_days"]}, ["forecast_days"]),
    _tool("toggle_chart", "展开/收起底部的气象剖面图。", "ui", {}, []),
    _tool("play_simulation", "控制沿航线的气象推演：播放、暂停、前进一帧、重置。",
          "ui", {k: _P[k] for k in ("action", "speed")}, ["action"]),
    _tool("clear_route", "清掉当前航线与规划结果，回到未规划状态。", "ui", {}, []),

    # ---- 计算（后端执行）----
    _tool("run_plan", "按当前起终点与途经点跑一次三档航线规划并评估。"
           "**一次调用会同时算出紧急/平衡/安全三档**，所以只在改了起终点、途经点、"
           "时间、载重等**输入**之后才调它；单纯想看另一档请用 set_priority，不要重跑。",
          "compute", {}, []),
    _tool("run_site_plan", "多航点物流：按当前载重与机型选最少的���继落地站并逐段复验。",
          "compute", {}, []),
    _tool("run_time_window", "扫描期望日期内湍流最低、全程安全的时间窗口。",
          "compute", {}, []),
    _tool("query_weather", "取指定地点在当前日期时刻的真实 TKE / EDR / 风温湿。",
          "compute", {"sites": _P["sites"]}, []),
    _tool("search_sites",
          "在 220 个站表条目里检索可用落地/备降点。**用户问「哪里能备降」「附近有什么机场」"
          "时必须用它去查，不要凭印象回答，也不要承诺以后再给**。",
          "compute", {k: _P[k] for k in ("query", "name", "km")}, []),
]

TOOL_NAMES = {t["name"] for t in TOOLS}
UI_TOOLS = {t["name"] for t in TOOLS if t["layer"] == "ui"}
COMPUTE_TOOLS = {t["name"] for t in TOOLS if t["layer"] == "compute"}


# ---------------------------------------------------------------- 参数校验

def _num(v: Any, lo: float, hi: float) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if lo <= f <= hi else None


def _alias(value: Any, table: Dict[str, str], aliases: Dict[str, str]) -> Optional[str]:
    if not isinstance(value, str):
        return None
    v = value.strip()
    if v in table:
        return v
    return aliases.get(v)


def validate_args(tool: str, raw: Any) -> Dict[str, Any]:
    """逐字段校验并归一化。**不合法的字段直接丢掉，不报错也不猜**。

    丢掉比报错重要：模型偶尔会给出界面上不存在的档位或图层，这时让它继续跑、
    由前端忽略即可；整个工具调用因为一个字段被拒反而会让对话卡住。
    """
    a: Dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}

    if tool == "toggle_overlay":
        layer = _alias(a.get("layer"), OVERLAY_KEYS, OVERLAY_ALIASES)
        on = a.get("on")
        if isinstance(on, str):
            on = on.strip().lower() in ("true", "1", "on", "yes", "开", "开启", "打开")
        return {"layer": layer, "on": bool(on)} if layer else {}

    if tool in ("focus_region",):
        lat, lon = _num(a.get("lat"), -90, 90), _num(a.get("lon"), -180, 180)
        zoom = _num(a.get("zoom"), 5, 14)
        km = _num(a.get("km"), 1, 500)
        out: Dict[str, Any] = {}
        if lat is not None and lon is not None:
            out["lat"], out["lon"] = lat, lon
        if isinstance(a.get("name"), str) and a["name"].strip():
            out["name"] = a["name"].strip()[:40]
        if zoom is not None:
            out["zoom"] = int(zoom)
        if km is not None:
            out["km"] = km
        return out

    if tool == "add_waypoint":
        out = {}
        if isinstance(a.get("name"), str) and a["name"].strip():
            out["name"] = a["name"].strip()[:40]
        idx = _num(a.get("index"), 1, 50)
        if idx is not None:
            out["index"] = int(idx)
        return out

    if tool in ("remove_waypoint", "update_waypoint"):
        idx = _num(a.get("index"), 1, 50)
        out = {"index": int(idx)} if idx is not None else {}
        if tool == "update_waypoint" and isinstance(a.get("name"), str) and a["name"].strip():
            out["name"] = a["name"].strip()[:40]
        return out

    if tool == "set_endpoints":
        idx = _num(a.get("index"), -1, 0)
        if idx is None:
            return {}
        out: Dict[str, Any] = {"index": int(idx)}
        if isinstance(a.get("name"), str) and a["name"].strip():
            out["name"] = a["name"].strip()[:40]
        lat, lon = _num(a.get("lat"), -90, 90), _num(a.get("lon"), -180, 180)
        if lat is not None and lon is not None:
            out["lat"], out["lon"] = lat, lon
        return out

    if tool == "switch_tab":
        tab = _alias(a.get("tab"), TABS, TAB_ALIASES)
        return {"tab": tab} if tab else {}

    if tool == "set_time":
        out = {}
        if isinstance(a.get("date"), str) and _DATE_RE.match(a["date"].strip()):
            out["date"] = a["date"].strip()
        if isinstance(a.get("time"), str) and _TIME_RE.match(a["time"].strip()):
            out["time"] = a["time"].strip()[:5]
        return out

    if tool == "set_priority":
        p = _alias(a.get("priority"), PRIORITIES, PRIORITY_ALIASES)
        return {"priority": p} if p else {}

    if tool == "set_payload_kg":
        v = _num(a.get("payload_kg"), 0, 5000)
        return {"payload_kg": v} if v is not None else {}

    if tool == "set_aircraft":
        return {"aircraft": a.get("aircraft")} if a.get("aircraft") in ("td550", "v2000cg") else {}

    if tool == "set_mission":
        m = _alias(a.get("mission"), MISSIONS, MISSION_ALIASES)
        return {"mission": m} if m else {}

    if tool == "set_speed":
        v = _num(a.get("speed"), 10, 400)
        return {"speed": v} if v is not None else {}

    if tool == "set_forecast_days":
        v = _num(a.get("forecast_days"), 0, 15)
        return {"forecast_days": int(v)} if v is not None else {}

    if tool == "play_simulation":
        act = _alias(a.get("action"), SIM_ACTIONS, SIM_ALIASES)
        out: Dict[str, Any] = {"action": act} if act else {}
        sp = _num(a.get("speed"), 10, 5000)
        if sp is not None:
            out["speed"] = int(sp)
        return out

    if tool == "query_weather":
        sites = a.get("sites")
        if isinstance(sites, list):
            names = [str(s).strip()[:40] for s in sites if isinstance(s, str) and s.strip()]
            return {"sites": names[:6]}
        return {}

    if tool == "search_sites":
        out = {}
        for k in ("query", "name"):
            if isinstance(a.get(k), str) and a[k].strip():
                out[k] = a[k].strip()[:40]
        km = _num(a.get("km"), 1, 500)
        if km is not None:
            out["km"] = km
        return out

    # toggle_chart / clear_route / run_* 不带参数
    return {}


# ---------------------------------------------------------------- 给模型看的清单

def catalog_text() -> str:
    """渲染成提示词里的一段工具说明。

    只写名称、用途和参数 —— **不写阈值、不写内部状态**。工具是「能做什么」，
    判据仍然只能来自工具返回的结果。
    """
    lines = []
    for t in TOOLS:
        props = t["params"]["properties"]
        args = ", ".join(f"{k}({v.get('type', 'any')})" for k, v in props.items()) or "无参数"
        where = "界面操作" if t["layer"] == "ui" else "数据计算"
        lines.append(f"- {t['name']}（{where}）：{t['description']} 参数：{args}")
    return "\n".join(lines)
