"""Agent 循环：一句话 -> 一串工具调用 -> 看到结果 -> 再决定。

**为什么是分步往返而不是一次出计划。** 工具调用的价值在于模型能看到执行结果：
「定位到曲松县」失败了，它才知道该换成哪个名字；「查了备降点但 50 km 内没有」，
它才不会编一个出来。一次出计划做不到这种反应。

**为什么是 SSE 往返而不是 WebSocket。** 这条链路是 Cloudflare → OpenResty → frp
→ 本机 9090 的纯 HTTP，WebSocket 会多一个没有必要的失败模式；而一步通常只有
1~4 次往返，连接复用省下来的时间远不如「每步独立可重试、不用维持长连接」值钱。

所以形态是：后端一轮只吐 tool_calls 然后**结束这次请求**；前端执行完工具，
带着 tool_results 重新 POST，由后端接着下一轮。整段对话的状态由前端保存
（messages + steps），后端保持无状态 —— 这样任何一步断了都能原地重试。
"""

import json
import os
import re
import urllib.request
from typing import Any, Dict, Iterator, List, Optional

from assessor import llm_available
from config import settings
from tools import TOOL_NAMES, catalog_text, validate_args

# 一次对话最多几轮工具调用。5 轮足够覆盖「开图层 -> 定位 -> 加点 -> 规划 -> 看结果」，
# 也不至于让模型在一个请求里兜圈。
MAX_STEPS = 5

# 与 intent/suggest 同一道防线：推理型模型会把思考写进 content，
# 不剥的话下面按 [] 截 JSON 会命中思考里提到的字段名，截出一段碎片。
_THINK_BLOCK = re.compile(
    r"<(think|thinking|thought|reasoning)>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)

# 端点在公网无鉴权：历史条数与单条字数都要封顶，否则一次请求就能把上下文
# 灌到网关的 token 上限，既慢又贵。
MAX_MESSAGES = 12
MAX_CHARS = 1200

SYSTEM_PROMPT = f"""你是无人机气象航线规划系统里的操作助手。你可以通过调用工具来真正操作界面，而不只是聊天。

【可用工具】
{catalog_text()}

【输出格式 —— 必须严格遵守】
需要调用工具时，**只输出一个 JSON 数组**，不要写任何别的字（不要写「好的，我来操作」这类开场白）：
[{{"tool": "工具名", "args": {{"参数名": 值}}}}]

示例（要调两个工具就输出两项）：
[{{"tool": "toggle_overlay", "args": {{"layer": "tke", "on": true}}}}, {{"tool": "focus_region", "args": {{"name": "曲松县"}}}}]

不需要调用工具、可以直接回答时，才输出普通中文正文。

【铁律】
1. 想改变界面或数据，**必须调用工具**，不许只在文字里描述你会怎么做。用户看到的每一条动作记录都对应一次真实的工具调用。
2. **严禁承诺任何未来动作**。不要说「我明天发给你」「稍后补充」「回头再告诉你」——你没有任何定时、后台或通知能力，做不到的事就直说做不到。
3. 数据里没有的，就说没有。用户问「哪里能备降」而你还没查过，就先调 search_sites 去查；查完确实没有，就回答「50 km 内没有可用备降点」。**绝不许自己编一条政策或一个数字**。
4. 数值、距离、风险等级、站点名称**只能来自工具返回的结果**。工具没给的，一个字都不要编。
5. 用户要的操作你做不到时（工具清单里没有），直接回答「我目前不具备该能力」，不要绕着解释。
6. 改了规划输入（起终点、途经点、时间、载重、机型）之后，如果用户想看结果，再调对应的 run_* 工具；不要凭改完的输入直接描述结果。
7. **用户明确说出来的每一个参数都必须先用工具设进去**（载重多少、几点起飞、什么机型、几个途经点），不能只在心里记下就当已经改好。参数没设就去规划，算出来的是另一组条件的结果。
8. **同一件事不要调两次。** 三档航线是一次规划同时算出来的，切换档位只调 set_priority 即可，**不要为了切档再调 run_plan**。
9. 回答用简洁中文，可用 Markdown 列表，控制在 300 字以内。不要输出思考过程、草稿或自我说明。"""


def _llm(messages: List[Dict[str, str]], max_tokens: int = 900) -> Optional[str]:
    """一次非流式调用，只取正文。异常一律吞掉返回 None。"""
    if not llm_available():
        return None
    body = {
        "model": settings.AI_MODEL,
        "messages": messages,
        "temperature": settings.AI_TEMPERATURE,
        "max_tokens": max_tokens,
    }
    thinking = (settings.AI_THINKING or "").strip().lower()
    if thinking in ("disabled", "adaptive"):
        body["thinking"] = {"type": thinking}
    try:
        key = os.environ.get(settings.AI_API_KEY_ENV or "UAV_AI_API_KEY", "")
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        url = settings.AI_BASE_URL.rstrip("/") + "/chat/completions"
        with urllib.request.urlopen(urllib.request.Request(
                url, data=json.dumps(body).encode("utf-8"),
                headers=headers, method="POST"),
                timeout=float(settings.AI_TIMEOUT_S)) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    content = data["choices"][0]["message"].get("content") or ""
    content = _THINK_BLOCK.sub("", content)
    return content.strip()


def _extract_json(text: str) -> Any:
    """从可能混着自然语言的输出里抠出 JSON。

    模型很爱在工具调用前加一句「好的，我来操作：」，更爱把工具调用写成
    ```json ... ``` 代码块。直接 `find("[")` 会命中正文里第一个方括号
    （例如 ``[layer=tke, on=true]``），截出来的根本不是 JSON —— 实测这一步
    静默失败会让整个 Agent 退化成「只会说不会做」。

    所以这里按候选逐个试：对象形态（取 tool_calls）与数组形态都认，
    再从每个 ``[`` 位置起试到最后一个 ``]``，谁先解析成功用谁。
    """
    if not text:
        return None
    # 去掉 ```json 代码块围栏，省得围栏里的内容干扰定位
    cleaned = text.replace("```json", "").replace("```", "")

    # 1) 对象形态
    ob, oe = cleaned.find("{"), cleaned.rfind("}")
    if ob >= 0 and oe > ob:
        try:
            obj = json.loads(cleaned[ob:oe + 1])
            if isinstance(obj, dict) and isinstance(obj.get("tool_calls"), list):
                return obj["tool_calls"]
        except Exception:
            pass

    # 2) 数组形态：逐个左括号位置试
    end = cleaned.rfind("]")
    if end < 0:
        return None
    i = cleaned.find("[")
    while 0 <= i < end:
        try:
            got = json.loads(cleaned[i:end + 1])
            if isinstance(got, list):
                return got
        except Exception:
            pass
        i = cleaned.find("[", i + 1)
    return None


def _parse_calls(content: str) -> List[Dict[str, Any]]:
    """取出工具调用列表，并逐个校验工具名与参数。"""
    raw = _extract_json(content)
    if not isinstance(raw, list):
        return []

    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("tool") or item.get("name")
        if name not in TOOL_NAMES:
            continue    # 模型编了个不存在的工具名：丢掉，不猜
        out.append({
            "tool": name,
            "args": validate_args(name, item.get("args") or item.get("arguments") or {}),
        })
        if len(out) >= 6:   # 一轮里最多 6 个，再多就是模型跑飞了
            break
    return out


def _build_messages(state: Dict[str, Any],
                    messages: List[Dict[str, Any]],
                    tool_results: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    system = SYSTEM_PROMPT
    if state:
        system += "\n\n【当前界面状态】\n" + json.dumps(state, ensure_ascii=False)[:1200]

    out: List[Dict[str, str]] = [{"role": "system", "content": system}]
    for m in (messages or [])[-MAX_MESSAGES:]:
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        out.append({
            "role": m["role"] if m.get("role") in ("user", "assistant") else "user",
            "content": content[:MAX_CHARS],
        })

    # 工具结果以 user 角色回灌：SSE 是单向的，这里就是「把结果告诉模型」的那一步。
    # 带上 tool_calls 的原文，让模型知道自己刚调过什么、结果如何。
    for tr in (tool_results or []):
        out.append({
            "role": "user",
            "content": "【工具执行结果】\n" + json.dumps(tr, ensure_ascii=False)[:1200],
        })
    return out


def run_agent(state: Dict[str, Any], messages: List[Dict[str, Any]],
              tool_results: Optional[List[Dict[str, Any]]] = None,
              round_index: int = 0) -> Iterator[Dict[str, Any]]:
    """一轮 Agent。产出 SSE 事件字典。

    事件类型：
      - ``{"type":"tool_calls","calls":[...]}``  这一步要调工具，等前端回结果
      - ``{"type":"delta","text":"..."}``         正文增量
      - ``{"type":"error","message":"..."}``      模型不可用
      - ``{"type":"done"}``                       结束

    ``tool_results`` 非空表示这是同一句用户输入的第 2 步及以后（带着上一步的执行结果）。
    ``round_index`` 是**第几轮**，由前端数着传进来。

    这里曾经拿 ``len(tool_results)`` 当轮数，结果第一轮返回 6 个工具调用就被判成
    「已调满」——模型第二轮要调 ``run_plan`` 时直接跳过，**原始 JSON 被当成答案
    显示给用户**。轮数必须由外部数着给，不能用「结果条数」这种会随一轮里工具个数
    跳变的量代替。
    """
    if not llm_available():
        yield {"type": "error", "message": "对话模型未启用，无法解析你的意图。"}
        yield {"type": "done"}
        return

    msgs = _build_messages(state, messages, tool_results or [])

    content = _llm(msgs)
    if content is None:
        yield {"type": "error", "message": "AI 服务调用失败，请稍后重试。"}
        yield {"type": "done"}
        return

    calls = _parse_calls(content)
    if calls and round_index < MAX_STEPS:
        yield {"type": "tool_calls", "calls": calls}
        yield {"type": "done"}
        return

    # 到这里要么本来就没要调工具，要么轮数用完了。两种情况都**不能把工具调用
    # 的原文当答案吐出去** —— 界面上突然冒出一段 [{"tool": ...}] 是最糟的失败形态。
    if any(t in content for t in TOOL_NAMES) and not (calls and round_index < MAX_STEPS):
        tail = (f"\n\n（已连续执行 {round_index} 轮工具调用，本轮先到这里。"
                "你可以直接告诉我下一步要做什么。）") if calls else ""
        yield {"type": "delta", "text": "（这轮的指令我没有执行完，工具调用也没能正常处理。" + tail + "）"}
        yield {"type": "done"}
        return

    yield {"type": "delta", "text": content or "（没有可执行的操作，也没有可回答的内容）"}
    yield {"type": "done"}
