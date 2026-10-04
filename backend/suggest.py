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
    "8. 想不出第三条怎么办：**宁可给三条角度不同但都在能力内的，也不要凑一条能力外的**。\n"
    "9. **绝对不要重复用户刚刚问过的问题**，也不要三条都围绕同一个角度"
    "（例：别三条都在问湍流峰值）。用户刚问过的会列在下面，避开它们，"
    "从**用户还没问过的角度**里挑。\n"
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


def _fmt_history(history: List[Dict[str, str]], limit: int = 12,
                 cap: int = 3000) -> str:
    """把完整对话压成一段纯文本，按时间正序。

    刻意只取文字：工具轨迹与图片附件不传。轨迹是给模型看「我干了什么」的，
    而胶囊要预判的是「用户接下来想问什么」—— 判断「哪些角度还没被问到」
    只需要问答往复本身，带上轨迹只是白烧几千 token。
    """
    rows: List[str] = []
    used = 0
    # 从最新往回收，装不下就停 —— 最近几轮才是判断「还剩什么角度」的依据。
    for turn in reversed(history or []):
        role = "用户" if turn.get("role") == "user" else "助手"
        text = str(turn.get("text") or "").strip()
        if not text:
            continue
        room = cap - used
        if room <= 80:
            break
        if len(text) > room:
            text = text[-room:]        # 掐头留尾：结论一般都在末尾
        rows.append(f"{role}：{text}")
        used += len(text) + 1
        if len(rows) >= limit:
            break
    return "\n".join(reversed(rows)) or "（无）"


def _llm_chips(task: str, state: Dict[str, Any], history: List[Dict[str, str]],
               keep: List[Dict[str, str]], count: int,
               payload: Optional[Dict[str, Any]]) -> Optional[List[Dict[str, str]]]:
    if not llm_available():
        return None

    # 「用户问过的」和「屏幕上还留着的」本质是同一类约束 —— 别再端一个一样的上来。
    asked = [str(t.get("text") or "") for t in (history or []) if t.get("role") == "user"]
    avoid = asked + [str(c.get("prompt") or "") for c in (keep or []) if c.get("prompt")]

    state_line = _STATE_HINT.get(task, task)
    if state.get("sidebar_tab") and state["sidebar_tab"] != task:
        state_line += f"（左栏当前停在「{state['sidebar_tab']}」页）"
    if not state.get("has_result"):
        state_line += "；目前还没有任何评估结果"
    if state.get("verdict"):
        state_line += f"；最近一次结论：{state['verdict']}"
    if state.get("level"):
        state_line += f"；风险等级：{state['level']}"

    if count < MAX_CHIPS:
        lead = (f"屏幕上还留着 {len(keep)} 条胶囊，用户刚点掉了其中一条。"
                f"这次**只补 {count} 条**，且必须与留着的那几条角度不同。\n")
    else:
        lead = ""

    user = (
        _SUGGEST_SYSTEM
        + f"\n\n{tools.SYSTEM_BOUNDARY}\n"
        + lead
        + f"【对话对象】{task}\n"
        + f"【当前状态】{state_line}\n"
        + f"【完整对话记录（判断「哪些角度还没被问到」的唯一依据）】\n"
        + _fmt_history(history) + "\n"
        + f"【绝对不要再生成的：用户问过的 + 屏幕上还留着的胶囊】\n"
        + ("\n".join(f"- {a}" for a in avoid[-8:]) or "（无）") + "\n"
        + "【可供提问的数据（只有这些是真的）】\n"
        + json.dumps(payload, ensure_ascii=False)[:1500]
        + f"\n\n请输出 JSON（恰好 {count} 条）："
    )

    req = {
        "model": settings.AI_MODEL,
        "messages": [{"role": "user", "content": user}],
        "temperature": 0.7,   # 这里要多样性，0.3 会每次都给出同一批
        # 补一条时也给 3 条的输出预算：去重会挡掉与「问过的/留着的」撞车的，
        # 让模型多想两条，才凑得出要的那 count 条。
        "max_tokens": 400 if count >= MAX_CHIPS else 220,
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
    # 一次给足 MAX_CHIPS：去重会挡掉一部分，让模型多想几条才凑得出要的 count 条。
    return _clean(raw.get("chips") if isinstance(raw, dict) else None)[:MAX_CHIPS]


def _norm(s: str) -> str:
    """归一化到可比形式：只留实义字符，丢掉标点、空格与全半角差异。

    比对「是不是同一个问题」必须先归一：模型这次写「哪一段的颠簸最厉害?」、
    下次写「哪一段的颠簸最厉害？」，用户那边发过来的又是另一种标点，
    原始字符串永远不相等，去重就成了摆设。
    """
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def _dedup(chips: List[Dict[str, str]], avoid: List[str]) -> List[Dict[str, str]]:
    """去掉重复的胶囊，以及**已经被问过 / 还亮在屏幕上**的同一个问题。

    后者是必须的：避让清单已经传进提示词了，但那只是「请模型避开」的请求，
    不是保证。实测同一段对话里连续四次生成，第一条胶囊全是「哪段最颠」类，
    其中一次与用户刚发出的问句一字不差 —— 用户点它等于把自己的话再问一遍。

    判定用归一化后的**包含**关系：短句被长句包住时也算重复。
    """
    seen: List[str] = []
    out: List[Dict[str, str]] = []
    for c in chips:
        p = _norm(c["prompt"])
        if not p:
            continue
        # 与「问过的 + 还留着的」比对：命中就丢弃这一条。
        # 后者不能省 —— 补出来的新胶囊若和留着的那两条撞车，屏幕上就并排
        # 摆着两个几乎一样的胶囊，等于白占一个位置。
        if any(p in _norm(a) or _norm(a) in p for a in avoid if a):
            continue
        # 与本批次已保留的比对：模型偶尔会把同一个问题写两遍
        if any(p in s or s in p for s in seen):
            continue
        seen.append(p)
        out.append(c)
    return out


def suggest_chips(task: str, payload: Optional[Dict[str, Any]],
                  history: List[Dict[str, str]], keep: List[Dict[str, str]],
                  count: int, state: Dict[str, Any]) -> Dict[str, Any]:
    """入口。返回 ``{chips, source}``；``source="rule"`` 表示模型没顶上。

    ``count`` = 本次要**新生成**几条：
      - 用户自己打字 → 3（上下文变了，整排重生成）
      - 用户点了某条胶囊 → 1（只补被吃掉的那一条，另外两条原样留着）
    ``keep`` 是要保留在屏幕上的旧胶囊，它们会进避让清单。
    """
    count = max(1, min(int(count or MAX_CHIPS), MAX_CHIPS))
    chips: List[Dict[str, str]] = []
    source = "rule"

    asked = [str(t.get("text") or "") for t in (history or []) if t.get("role") == "user"]
    avoid = asked + [str(c.get("prompt") or "") for c in (keep or []) if c.get("prompt")]

    if settings.AI_ENABLED:
        got = _llm_chips(task, state, history, keep, count, payload)
        if got:
            chips, source = got, "llm"

    if not chips:
        chips = _rule_chips(task, state)

    # 去重放在最后：LLM 与规则兜底两条路都要过，否则规则句式里那些固定项
    # （「哪一段最危险」）照样会在用户刚问过时原样弹回来。
    chips = _dedup(chips, avoid)[:count]

    # 兜底也凑不满时，宁可少给也不要拿空胶囊糊弄用户
    return {"chips": chips, "source": source}
