"""对话层的意图识别：一句话 -> 模式 + 实体。

对话是全站唯一入口（顶部只留对话，没有并排的「一句话任务」输入框），所以这里既要回答
「用户想要哪个模式」，也要把该模式需要的实体一次抽全 —— 前端拿到响应后只需调
``store.applyIntent()``，切页签 / 回填 / 触发规划都在那一处发生。

**裁判只有一个：LLM。** 关键词表在这里是没有胜算的 —— 判据天然互相打架
（「沿途」既是多航点信号也是"问当前结果"信号；"避开大风"里的「大风」匹配不到
时间意图），加词永远补不完，只会被下一种没列到的说法击穿。所以主路径是带
few-shot 的提示词，示例直接取 PRD 里那几句现成的话。

字段口径仍以 ``sites`` 为唯一来源：兜底层直接复用 ``sites._rule_parse``，
不在这里另建一套载重 / 机型 / 起终点的关键词表（两处各写一遍必然漂移）。
兜底层的存在理由与 ``sites.parse_task``、``assessor`` 的降级纪律一致：LLM
不可用时左栏联动不该整个塌掉，它只做**高辨识度**的窄判断，不做通用语义猜测。
"""

import json
import os
import re
import urllib.request
from typing import Any, Dict, List, Optional

import geo
import sites
from assessor import llm_available
from config import settings

# 意图取值：前三个会触发左侧页签联动，后两个是纯问答。
#   plan        单起点单终点，路径规划
#   time_window 找最安全的起飞时刻
#   site        多目的地 / 物流分发，多航点
#   weather     问 TKE / EDR / 某地此刻气象（不切页签，单独取数）
#   chat        就当前结果追问
INTENTS = ("plan", "time_window", "site", "weather", "chat")

# 联动型意图：前端据此决定要不要切页签并发起规划。
LINKING_INTENTS = ("plan", "time_window", "site")

# 抽给模型的字段白名单。多了的每一项都必须在 ``_coerce_fields`` 里有对应校验，
# 否则模型能编出界面上不存在的档位（公网无鉴权，见 AGENTS.md）。
_ALLOWED_FIELDS = (
    "start_name", "end_name", "place_names",
    "payload_kg", "aircraft", "mission",
    "date", "time", "priority", "forecast_days",
)

_AIRCRAFT_KEYS = tuple(sites.AIRCRAFT_PACKS)
_PRIORITIES = ("urgent", "normal", "economy")
_MISSIONS = ("cargo", "crew")


# ==================== 地名全集 ====================

def _place_names() -> List[str]:
    """可被提及的地名全集：4 个城市基点 + 站表全部条目。

    站表里的「曲松县 / 朗县 / 米林县 / 加查县」都在，提示词要给全量：
    截断过的话模型不是认不出就只能编，而这条链路后面要直接发规划请求。
    """
    names: List[str] = [str(c.get("name")) for c in geo.CITIES if c.get("name")]
    try:
        for s in sites.public_sites().get("sites", []):
            n = str(s.get("name") or "").strip()
            if n and n not in names:
                names.append(n)
    except Exception:
        # 站表读不到就只用城市：少认几个地名好过整个意图识别塌掉
        pass
    return names


# ==================== LLM 裁判 ====================

_INTENT_PROMPT = """你是无人机航线规划系统的意图识别器。只输出一个 JSON 对象，不要解释、不要 Markdown、不要思考过程。

intent 只能取以下五个之一：
  "plan"        规划一条单起点到单终点的航线
  "time_window" 问什么时间起飞最安全、避开大风、未来几天哪个时段好
  "site"        多个目的地之间的物流分发、送药、中继、沿途多个落点
  "weather"     问气象实况：TKE、EDR、湍流强度、风速、温度、湿度
  "chat"        就当前已有的评估结果追问（没有提出新的操作要求）

fields 的键与取值：
  "start_name"    地名字符串或 null（出发地）
  "end_name"      地名字符串或 null（目的地）
  "place_names"   字符串数组（中间途经地点，没有就空数组）
  "payload_kg"    数字或 null（载重，公斤）
  "aircraft"      "__AIRCRAFT__" | null
  "mission"       "cargo"（运货）| "crew"（载人）| null
  "date"          "YYYY-MM-DD" 或 null
  "time"          "HH:MM" 或 null
  "priority"      "urgent"（紧急）| "normal"（平衡）| "economy"（安全）| null
  "forecast_days" 整数或 null（用户要求推演的天数）

判断规则（按顺序走，命中即止）：
1) 原文提到具体物理量（TKE、EDR、湍流、风速、气温、湿度…）且问的是某地此刻情况
   -> weather。若问的是「这条航线」「当前」「沿途」的既有结果 -> chat
2) 原文在问什么时候起飞、避开大风、未来几天哪个时段好 -> time_window
3) 原文提到载重（如「200 公斤」）、或货物/送药/配送/物流/补给/中继/多个落点
   -> site。载重决定机型与航段划分，即使只有一个目的地也属于多航点调度
4) 其余提到航线、路径、规划、能否飞的 -> plan（纯航线规划不涉及载重与货物）
5) 其余（没有提出新的操作要求）-> chat

原文没说到的字段必须填 null 或空数组，禁止推测、禁止补默认值、禁止编造地名。

可选地名（照抄其中之一，不要自己造）：__PLACES__

当前基准日期：__BASE_DATE__

【示例】
输入：从拉萨送 200 公斤兽药到昌都
输出：{"intent":"site","fields":{"start_name":"拉萨","end_name":"昌都","place_names":[],"payload_kg":200,"aircraft":null,"mission":"cargo","date":null,"time":null,"priority":null,"forecast_days":null}}

输入：评估明天下午拉萨到林芝的飞行风险
输出：{"intent":"time_window","fields":{"start_name":"拉萨","end_name":"林芝","place_names":[],"payload_kg":null,"aircraft":null,"mission":null,"date":"__BASE_PLUS1__","time":"14:00","priority":null,"forecast_days":7}}

输入：拉萨现在的 TKE 是多少
输出：{"intent":"weather","fields":{"start_name":null,"end_name":null,"place_names":["拉萨"],"payload_kg":null,"aircraft":null,"mission":null,"date":null,"time":null,"priority":null,"forecast_days":null}}

输入：帮我看看哪一段最危险
输出：{"intent":"chat","fields":{"start_name":null,"end_name":null,"place_names":[],"payload_kg":null,"aircraft":null,"mission":null,"date":null,"time":null,"priority":null,"forecast_days":null}}

输入：规划一条从日喀则到林芝的最安全航线
输出：{"intent":"plan","fields":{"start_name":"日喀则","end_name":"林芝","place_names":[],"payload_kg":null,"aircraft":null,"mission":null,"date":null,"time":null,"priority":"economy","forecast_days":null}}

用户输入：__INPUT__"""


def _build_prompt(text: str, base_date: Optional[str]) -> str:
    base = base_date or ""
    plus1 = ""
    if re.match(r"\d{4}-\d{2}-\d{2}", base):
        import datetime as _dt
        y, mo, d = (int(x) for x in base.split("-"))
        plus1 = (_dt.date(y, mo, d) + _dt.timedelta(days=1)).isoformat()
    else:
        plus1 = "null"

    prompt = _INTENT_PROMPT
    prompt = prompt.replace("__AIRCRAFT__", " | ".join(_AIRCRAFT_KEYS))
    prompt = prompt.replace("__PLACES__", "、".join(_place_names()))
    prompt = prompt.replace("__BASE_DATE__", base or "未知（相对时间请填 null）")
    prompt = prompt.replace("__BASE_PLUS1__", plus1)
    return prompt.replace("__INPUT__", text)


# 推理型模型会把思考写进 content。这一层必须与 assessor 的 ThinkTagFilter 同源处理：
# 少了它，下面 find("{") 会命中思考里提到的字段名，截出一段根本不是 JSON 的碎片
# （实测「从拉萨送 200 公斤兽药到昌都」有 4 成概率栽在这）。
_THINK_BLOCK = re.compile(
    r"<(think|thinking|thought|reasoning|REASONING_SCRATCHPAD)>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
# 网关忽略 thinking 参数时，思考标签会顶到 max_tokens 被截断而没有闭合，
# 这种情况后面根本没有 JSON，让它自然解析失败即可，不要试图救。


def _strip_thinking(text: str) -> str:
    return _THINK_BLOCK.sub("", text or "")


def _llm_parse(text: str, base_date: Optional[str]) -> Optional[Dict[str, Any]]:
    """一次调用同时拿意图和实体。异常一律吞掉返回 None，由兜底层接手。"""
    if not llm_available():
        return None

    body = {
        "model": settings.AI_MODEL,
        "messages": [{"role": "user", "content": _build_prompt(text, base_date)}],
        "temperature": 0.0,
        # 比 assessor 的报告上限低：这里只要一个 JSON 对象。
        # 留出的余量是给「网关仍回传思考」的情况的，思考用完就等于没有 JSON。
        "max_tokens": 800,
    }
    # 与 assessor.chat_request_body 同一套请求侧开关（取空时不发该字段，兼容
    # 不认识该参数的网关）。
    thinking = (settings.AI_THINKING or "").strip().lower()
    if thinking in ("disabled", "adaptive"):
        body["thinking"] = {"type": thinking}
    key = os.environ.get(settings.AI_API_KEY_ENV or "UAV_AI_API_KEY", "")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    url = settings.AI_BASE_URL.rstrip("/") + "/chat/completions"
    try:
        with urllib.request.urlopen(urllib.request.Request(
                url, data=json.dumps(body).encode("utf-8"),
                headers=headers, method="POST"),
                timeout=float(settings.AI_TIMEOUT_S)) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = _strip_thinking(data["choices"][0]["message"]["content"] or "")
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end <= start:
            return None
        raw = json.loads(content[start:end + 1])
    except Exception:
        return None
    return raw if isinstance(raw, dict) else None


def _coerce_fields(raw: Dict[str, Any]) -> Dict[str, Any]:
    """类型与取值域校验：不合法的字段一律当没给。

    这不是意图判断，是入口闸门。意图识别要直接触发一次三档 A*，而端点在公网
    无鉴权，所以 ``priority:"super_safe"``、``payload_kg:"两百"`` 这类值不能
    混进规划请求 —— 让它们静默变成 None，比让它们流到界面安全得多。
    """
    out: Dict[str, Any] = {k: None for k in _ALLOWED_FIELDS}
    out["place_names"] = []

    for key in ("start_name", "end_name"):
        v = raw.get(key)
        out[key] = str(v).strip() if isinstance(v, str) and v.strip() else None

    places = raw.get("place_names")
    if isinstance(places, list):
        out["place_names"] = [
            str(x).strip() for x in places if isinstance(x, str) and x.strip()
        ][:8]

    v = raw.get("payload_kg")
    try:
        out["payload_kg"] = float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        out["payload_kg"] = None

    out["aircraft"] = raw.get("aircraft") if raw.get("aircraft") in _AIRCRAFT_KEYS else None
    out["mission"] = raw.get("mission") if raw.get("mission") in _MISSIONS else None
    out["priority"] = raw.get("priority") if raw.get("priority") in _PRIORITIES else None

    v = raw.get("date")
    out["date"] = v if isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", v) else None
    v = raw.get("time")
    out["time"] = v if isinstance(v, str) and re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v) else None

    v = raw.get("forecast_days")
    try:
        days = int(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        days = None
    out["forecast_days"] = days if days is not None and 0 < days <= 15 else None
    return out


# ==================== 兜底层 ====================
#
# 判据只取高辨识度的窄信号，**不做通用语义猜测**：判错模式会静默切错页签、
# 发错请求，比不联动更糟。拿不准就归 chat，交给用户手动操作左栏。

_FB_TIME = ("什么时候", "何时", "几点", "时间窗口", "最佳时间", "最优时间",
            "避开", "未来几天", "这几天", "推荐时刻", "起飞时间")
_FB_WEATHER = ("tke", "edr", "湍流", "风速", "风力", "风场", "气温", "温度",
               "湿度", "水汽", "气压", "降水", "能见度", "天气", "气象")
_FB_PLAN = ("规划", "航线", "路径", "能不能飞", "能否飞", "可不可飞", "飞过去", "可行")


def _fallback(text: str) -> Dict[str, Any]:
    """LLM 不可用时的窄兜底。字段全部委托 ``sites._rule_parse``，不另建关键词表。"""
    base = sites._rule_parse(text or "")
    fields: Dict[str, Any] = {k: None for k in _ALLOWED_FIELDS}
    fields["place_names"] = []
    for key in ("payload_kg", "aircraft", "mission", "start_name", "end_name"):
        fields[key] = base.get(key)

    t = (text or "").lower()
    has_weight = fields["payload_kg"] is not None
    two_places = bool(fields["start_name"] and fields["end_name"])

    # 多航点：只认「带载重 + 起终点齐全」这一种高辨识度组合，
    # 再加「送/物流/中继」这类不会被别的意图抢走的动词。
    if has_weight and two_places and any(
        k in t for k in ("送", "运", "货", "物流", "中继", "补给", "分发", "药", "邮")
    ):
        intent = "site"
    elif any(k in t for k in _FB_TIME):
        intent = "time_window"
    elif any(k in t for k in _FB_WEATHER) and not any(
        k in t for k in ("这条", "当前", "沿途", "航段")
    ):
        intent = "weather"
    elif two_places and any(k in t for k in _FB_PLAN):
        intent = "plan"
    else:
        intent = "chat"

    return {"intent": intent, "fields": fields, "matched": base.get("matched", [])}


# ==================== 入口 ====================

# PRD 3.2 需要的追问文案：缺什么问什么，一次问全，别挤牙膏。
_CLARIFY = {
    "plan": "请告诉我起飞地和目的地（例如「从拉萨到林芝」），我就能开始规划。",
    "time_window": "请告诉我这条航线的起点和终点（例如「拉萨到昌都」），我来扫最安全的起飞时刻。",
    "site": "请补充任务信息：出发地、目的地，以及载重和货物类型（例如「从拉萨送 200 公斤兽药到昌都」）。",
    "weather": "请告诉我想看哪个地点的实况（例如「拉萨现在的 TKE 是多少」）。",
}

_REQUIRED = {
    "plan": ("start_name", "end_name"),
    "time_window": ("start_name", "end_name"),
    "site": ("start_name", "end_name", "payload_kg"),
}


def detect_intent(text: str, base_date: Optional[str] = None) -> Dict[str, Any]:
    """入口。返回 ``{intent, fields, missing, clarify, source, available}``。

    ``available=False`` 表示 LLM 不可用、只走了窄兜底：前端据此在对话里如实
    说明，而不是让用户以为模型真的理解了他的话。
    """
    raw = _llm_parse(text or "", base_date) if settings.AI_ENABLED else None
    source, available = ("llm", True) if raw is not None else ("rule", False)

    if raw is not None:
        fields = _coerce_fields(raw.get("fields") or {})
        intent = raw.get("intent") if raw.get("intent") in INTENTS else "chat"
        matched: List[str] = []
    else:
        fb = _fallback(text or "")
        intent, matched = fb["intent"], fb["matched"]
        # 兜底层抽到的起终点要保住，但不能让它覆盖字段的其余部分
        fields = _coerce_fields(fb["fields"])
        fields = {k: (fb["fields"].get(k) if fb["fields"].get(k) is not None else fields[k])
                  for k in _ALLOWED_FIELDS}
        fields["place_names"] = fb["fields"].get("place_names") or []

    missing: List[str] = []
    for key in _REQUIRED.get(intent, ()):
        if fields.get(key) in (None, "", []):
            missing.append(key)
    # 多航点缺载重不能当 0 送进后端：0 重量会算出一条"没意义的路线"，
    # 那不是用户的意思，先问一句更诚实。
    if intent == "site" and "payload_kg" not in missing and not fields.get("payload_kg"):
        missing.append("payload_kg")

    return {
        "intent": intent,
        "fields": fields,
        "missing": missing,
        "clarify": _CLARIFY.get(intent) if missing else None,
        "source": source,
        "available": available,
        "matched": matched,
    }
