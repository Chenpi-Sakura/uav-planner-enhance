"""后续建议胶囊：站在用户视角预测下一步最可能想说的话。

对话结束���输入框上方要给 3 个胶囊。这里让模型看「上一轮助手说了什么 + 左栏现在
是什么状态 + 用户最近几句问的什么」，然后替用户预判下一步 —— 这比按状态硬编码
句式更贴近真实使用顺序（用户自己也不知道该问什么，但模型能从结果里看出哪块
还没讲、哪个数字还没给）。

端点在公网无鉴权（见 AGENTS.md），所以三条防线：条数与长度都封顶、只接受结构
完整的对象、LLM 挂了回落到规则句式（降级纪律与 ``assessor`` / ``sites`` 一致）。
"""

import json
import re
from typing import Any, Dict, List, Optional

import tools
from assessor import llm_available
from config import settings

MAX_CHIPS = 3
MAX_LABEL = 14
MAX_PROMPT = 60

_SUGGEST_SYSTEM = (
    "你是无人机航线规划助手的对话引导器。你的任务是预测用户下一步最可能想问什么，"
    "而不是回答问题。只输出一个 JSON 对象，不要解释、不要 Markdown、不要思考过程。\n"
    '格式：{"chips":[{"label":"胶囊上显示的短句","prompt":"点击后填入并发送的完整问句"}]}\n'
    "要求：\n"
    f"1. 恰好 {MAX_CHIPS} 条，彼此角度不同（例：追细节 / 换方案 / 要结论）\n"
    f"2. label 不超过 {MAX_LABEL} 个字，是 prompt 的浓缩，不要重复用词啰嗦\n"
    f"3. prompt 是一句完整的自然语言问句，不超过 {MAX_PROMPT} 个字，能直接发送\n"
    "4. 必须用中文口语，像用户真的会打出来的话，不要写成指令或系统提示\n"
    "5. 只能围绕下方给出的数据与地名提问，禁止编造数值、航段名、地名\n"
    "6. 不要出现「JSON」「数据」「上下文」这类词\n"
    "7. **每一条都必须落在系统现有能力之内**（边界见下）。"
    "系统没有的能力一个字都不要提 —— 胶囊是用户点一下就会问出口的问题，"
    "凭空造一个「电池够不够飞完」「能耗剖面」之类，"
    "等于主动把用户领到一个回答不了的地方。\n"
    "8. 想不出第三条怎么办：**宁可给三条角度不同但都在能力内的，也不要凑一条能力外的**。"
)

_STATE_HINT = {
    "plan": "路径规划页（有/无规划结果，用户在做单起点到单终点的航线）",
    "time_window": "时间窗口页（找最安全的起飞时刻与推演未来几天）",
    "site": "多航点页（物流分发、中继站链、载重与机型）",
    "weather": "气象实况问答（用户在问 TKE、EDR、风温湿）",
}


def _rule_chips(task: str, state: Dict[str, Any]) -> List[Dict[str, str]]:
    """LLM 不可用时的兜底句式。只按「哪一页 + 有没有结果」给，不猜细节。"""
    has = bool(state.get("has_result"))
    if task == "time_window":
        cands = [
            ("这个推荐时刻为什么最安全", "为什么推荐这个起飞时刻？"),
            ("有没有备用时段", "除了这个时段，还有哪些备用时段？"),
            ("未来几天趋势如何", "未来几天的风险趋势是怎样的？"),
        ]
    elif task == "weather":
        cands = [
            ("风速温度怎么样", "那里的风速和温度现在怎么样？"),
            ("EDR 是多少", "那里的 EDR 具体是多少？"),
            ("换个地点看看", "帮我看看昌都现在的 TKE 和 EDR"),
        ]
    elif task == "site":
        cands = [
            ("总航时多少", "现在的预计总航时是多少？"),
            ("哪一段最危险", "帮我看看哪一段最危险？"),
            ("换一站重规划", "把第二站换成加查县重新规划"),
        ]
    elif has:
        cands = [
            ("哪一段最危险", "帮我看看哪一段最危险？"),
            ("三档有什么区别", "对比一下紧急、平衡、安全三种模式的区别"),
            ("生成飞行报告", "生成飞行建议报告"),
        ]
    else:
        cands = [
            ("从拉萨到林芝", "规划一条从拉萨到林芝的航线"),
            ("什么时候飞最安全", "拉萨到昌都什么时候起飞最安全？"),
            ("送药到昌都", "从拉萨送 200 公斤兽药到昌都"),
        ]
    return [{"label": a, "prompt": b} for a, b in cands[:MAX_CHIPS]]


def _clean(raw: Any) -> List[Dict[str, str]]:
    """只留结构完整的项：label 与 prompt 都得是非空字符串且在长度内。"""
    out: List[Dict[str, str]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        prompt = str(item.get("prompt") or "").strip()
        if not label or not prompt:
            continue
        # 去掉模型偶尔带上的 markdown 包裹，胶囊上显示会很难看
        label = re.sub(r"[*`#>\[\]]", "", label)[:MAX_LABEL]
        prompt = re.sub(r"[*`#\[\]]", "", prompt)[:MAX_PROMPT]
        if not label or not prompt:
            continue
        out.append({"label": label, "prompt": prompt})
        if len(out) >= MAX_CHIPS:
            break
    # 提示词已经约束过一遍，这里是输出侧的兜底：宁可少给一条，
    # 也不把「点了就会问出口、而系统答不了」的问题摆到用户面前。
    return [c for c in out if not _out_of_scope(c["prompt"])]


# 明显超出系统能力的主题词。**只拦「用户一看就知道系统没有」的**，
# 不做语义判断 —— 宁可漏拦（顶多少一条胶囊），不可误拦（把好问题删了）。
_OUT_OF_SCOPE = (
    "电池", "能耗", "能量消耗", "续航", "电量", "充电", "油量", "燃油量",
    "载荷余量", "载货剩余", "货物重量", "配平", "重心", "续航里程",
    "噪声", "噪音", "分贝", "空域申请", "空域批复", "飞行许可", "报批",
    "气象预报", "未来天气", "历史天气", "实测数据", "外场",
)


def _out_of_scope(prompt: str) -> bool:
    low = (prompt or "").lower()
    return any(w in low for w in _OUT_OF_SCOPE)


def _llm_chips(task: str, state: Dict[str, Any], last: str,
               recent: List[str], payload: Optional[Dict[str, Any]]) -> Optional[List[Dict[str, str]]]:
    if not llm_available():
        return None

    state_line = _STATE_HINT.get(task, task)
    if state.get("sidebar_tab") and state["sidebar_tab"] != task:
        state_line += f"（左栏当前停在「{state['sidebar_tab']}」页）"
    if not state.get("has_result"):
        state_line += "；目前还没有任何评估结果"
    if state.get("verdict"):
        state_line += f"；最近一次结论：{state['verdict']}"
    if state.get("level"):
        state_line += f"；风险等级：{state['level']}"

    user = (
        _SUGGEST_SYSTEM
        + f"\n\n{tools.SYSTEM_BOUNDARY}\n"
        + f"【对话对象】{task}\n"
        + f"【当前状态】{state_line}\n"
        + f"【上一轮助手回复】\n{(last or '（无）')[:600]}\n"
        + f"【用户最近问过】\n{'；'.join(recent[-3:]) or '（无）'}\n"
        + "【可供提问的数据（只有这些是真的）】\n"
        + json.dumps(payload, ensure_ascii=False)[:1500]
        + "\n\n请输出 JSON："
    )

    req = {
        "model": settings.AI_MODEL,
        "messages": [{"role": "user", "content": user}],
        "temperature": 0.7,   # 这里要多样性，0.3 会每次都给出同一批
        "max_tokens": 400,
    }
    thinking = (settings.AI_THINKING or "").strip().lower()
    if thinking in ("disabled", "adaptive"):
        req["thinking"] = {"type": thinking}

    try:
        import os
        import urllib.request
        key = os.environ.get(settings.AI_API_KEY_ENV or "UAV_AI_API_KEY", "")
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        url = settings.AI_BASE_URL.rstrip("/") + "/chat/completions"
        with urllib.request.urlopen(urllib.request.Request(
                url, data=json.dumps(req).encode("utf-8"),
                headers=headers, method="POST"),
                timeout=float(settings.AI_TIMEOUT_S)) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"] or ""
        # 与 intent 同一道防线：思考里会提到 {"label"…}，不剥就会截出碎片
        content = re.sub(
            r"<(think|thinking|thought|reasoning)>.*?</\1>", "", content,
            flags=re.DOTALL | re.IGNORECASE)
        s, e = content.find("{"), content.rfind("}")
        if s < 0 or e <= s:
            return None
        raw = json.loads(content[s:e + 1])
    except Exception:
        return None
    return _clean(raw.get("chips") if isinstance(raw, dict) else None)


def suggest_chips(task: str, payload: Optional[Dict[str, Any]], last: str,
                  recent: List[str], state: Dict[str, Any]) -> Dict[str, Any]:
    """入口。返回 ``{chips, source}``；``source="rule"`` 表示模型没顶上。"""
    chips: List[Dict[str, str]] = []
    source = "rule"

    if settings.AI_ENABLED:
        got = _llm_chips(task, state, last, recent, payload)
        if got:
            chips, source = got, "llm"

    if not chips:
        chips = _rule_chips(task, state)
    # 兜底也凑不满 3 条时，宁可少给也不要拿空胶囊糊弄用户
    return {"chips": chips[:MAX_CHIPS], "source": source}
