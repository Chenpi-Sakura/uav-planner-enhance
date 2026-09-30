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

import assessor
import tools
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

想改变界面或数据就调用工具；只是解释、回答、复述已有结果就直接说话。工具由系统以结构化方式提供，不要自己把工具调用写成 JSON 文本。

【铁律】
1. 用户看到的每一条动作记录都对应一次真实的工具调用，所以**不要只在文字里描述你会怎么做**——要做就调工具。
2. **严禁承诺任何未来动作**。不要说「我明天发给你」「稍后补充」「回头再告诉你」——你没有任何定时、后台或通知能力，做不到的事就直说做不到。
3. 数据里没有的，就说没有。用户问「哪里能备降」而你还没查过，就先调 search_sites 去查；查完确实没有，就回答「50 km 内没有可用备降点」。**绝不许自己编一条政策或一个数字**。
4. 数值、距离、风险等级、站点名称**只能来自工具返回的结果**。工具没给的，一个字都不要编。
5. 用户要的操作你做不到时（工具清单里没有），直接回答「我目前不具备该能力」，不要绕着解释。
6. 改了规划输入（起终点、途经点、时间、载重、机型）之后，如果用户想看结果，再调对应的 run_* 工具；不要凭改完的输入直接描述结果。
7. **用户明确说出来的每一个参数都必须先用工具设进去**（载重多少、几点起飞、什么机型、几个途经点），不能只在心里记下就当已经改好。参数没设就去规划，算出来的是另一组条件的结果。
8. **同一件事不要调两次。** 三档航线是一次规划同时算出来的，切换档位只调 set_priority 即可，**不要为了切档再调 run_plan**。
9. 回答用简洁中文，可用 Markdown 列表，控制在 300 字以内。不要输出思考过程、草稿或自我说明。"""


def _iter_sse_full(resp) -> Iterator[Dict[str, Any]]:
    """读一条 OpenAI 兼容 SSE 流，同时产出正文片段与工具调用分片。

    没用 ``assessor._iter_sse_content``：那个只取 ``delta.content``，会把
    ``delta.tool_calls`` 整个丢掉。这里两个都要。

    ``delta.tool_calls`` 是**分片**到达的（实测一轮 10 个 SSE 事件里有 1 个带它），
    ``function.arguments`` 还是 JSON 字符串的片段，必须按 index 拼完再解析。
    """
    partial: Dict[int, Dict[str, str]] = {}
    for raw_line in resp:
        line = raw_line.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        body = line[5:].strip()
        if not body or body == "[DONE]":
            continue
        try:
            parsed = json.loads(body)
        except Exception:
            continue
        for choice in (parsed.get("choices") or []):
            delta = choice.get("delta") or {}
            if delta.get("content"):
                yield {"kind": "text", "text": delta["content"]}
            for tc in (delta.get("tool_calls") or []):
                slot = partial.setdefault(int(tc.get("index", 0)), {"name": "", "args": ""})
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["args"] += fn["arguments"]
    for idx in sorted(partial):
        yield {"kind": "call", "name": partial[idx]["name"], "args_raw": partial[idx]["args"]}


def _llm_stream(msgs: List[Dict[str, str]]) -> Iterator[Dict[str, Any]]:
    """流式跑一轮。

    复用 ``assessor.chat_request_body`` / ``_open_stream`` 而不是自己拼请求体：
    请求侧的 ``thinking: {"type": "disabled"}``（模型不思考）与响应侧的
    ``<think>`` 剥离都在那边统一处理过，不重复发明。

    实测 MiniMax-M3 走原生 function calling：工具调用落在 ``delta.tool_calls``，
    此时 ``delta.content`` 为空 —— **正文与工具调用由协议保证分离**。所以正文
    可以逐段立即外发（真流式），不需要「看首字符是不是 ``[``」那种猜测式分流。
    """
    body = assessor.chat_request_body(msgs)
    body["tools"] = tools.openai_tools()
    body["tool_choice"] = "auto"
    resp = assessor._open_stream(body)
    try:
        for ev in _iter_sse_full(resp):
            yield ev
    finally:
        try:
            resp.close()
        except Exception:
            pass


def _calls_from_stream(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把攒下的工具调用分片变成可执行的调用列表。

    工具名不在清单里、或参数不是合法 JSON 的，直接丢掉：这一轮就当它没调工具，
    交给下一轮重新判断。宁可少调也不要执行一个界面上不存在的东西。
    """
    out: List[Dict[str, Any]] = []
    for ev in events:
        if ev.get("kind") != "call":
            continue
        name = (ev.get("name") or "").strip()
        if name not in tools.TOOL_NAMES:
            continue
        raw = (ev.get("args_raw") or "").strip()
        try:
            args = json.loads(raw) if raw else {}
        except Exception:
            args = {}
        if not isinstance(args, dict):
            args = {}
        out.append({"tool": name, "args": tools.validate_args(name, args)})
        if len(out) >= 6:
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

    # 正文逐段立即外发（真流式）；工具调用攒到这一轮读完再一次性给前端执行。
    collected: List[Dict[str, Any]] = []
    said_anything = False
    try:
        for ev in _llm_stream(msgs):
            if ev.get("kind") == "text":
                said_anything = True
                yield {"type": "delta", "text": ev["text"]}
            else:
                collected.append(ev)
    except Exception as exc:
        yield {"type": "error", "message": f"AI 服务调用失败（{type(exc).__name__}），请稍后重试。"}
        yield {"type": "done"}
        return

    calls = _calls_from_stream(collected)

    if calls and round_index < MAX_STEPS:
        yield {"type": "tool_calls", "calls": calls}
        yield {"type": "done"}
        return

    # 走到这里：要么本来就是要直接回答（正文已逐段流出），要么轮数用完了模型还想调工具。
    if calls:
        # 注意别把这条写成「我不具备该能力」：能力是有的，只是这轮用完了 ——
        # 说成能力缺失会把用户引向错误结论（换个说法重试也还是不行）。
        yield {"type": "delta", "text":
               f"\n\n（已连续执行 {round_index} 轮工具调用，本轮先到这里。"
               "你可以直接告诉我下一步要做什么。）"}
        yield {"type": "done"}
        return

    if not said_anything:
        # 一个字都没出、又没有工具调用：如实说做不到，不编也不装死。
        yield {"type": "delta", "text": "这一步我目前不具备对应的操作能力。"
                                       "可以说清楚想达成什么，我用现有能力来做。"}
    yield {"type": "done"}
