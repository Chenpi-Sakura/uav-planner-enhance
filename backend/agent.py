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
from typing import Any, Dict, Iterator, List, Optional, Tuple

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

# 端点在公网无鉴权：一次请求能塞多少图、每张多大，都要封顶。
# 多模态按图计 token，不封顶等于把额度敞开着给人刷。
MAX_IMAGES = 3
MAX_IMAGE_CHARS = 2_500_000

SYSTEM_PROMPT = f"""你是无人机气象航线规划系统里的操作助手。你可以通过调用工具来真正操作界面，而不只是聊天。

【可用工具】
{catalog_text()}

想改变界面或数据就调用工具；只是解释、回答、复述已有结果就直接说话。工具由系统以结构化方式提供，不要自己把工具调用写成 JSON 文本。

{tools.SYSTEM_BOUNDARY}

【铁律】
1. 用户看到的每一条动作记录都对应一次真实的工具调用，所以**不要只在文字里描述你会怎么做**——要做就调工具。
2. **严禁承诺任何未来动作**。不要说「我明天发给你」「稍后补充」「回头再告诉你」——你没有任何定时、后台或通知能力，做不到的事就直说做不到。
3. 数据里没有的，就说没有。用户问「哪里能备降」而你还没查过，就先调 search_sites 去查；查完确实没有，就回答「50 km 内没有可用备降点」。**绝不许自己编一条政策或一个数字**。
4. 数值、距离、风险等级、站点名称**只能来自工具返回的结果**。工具没给的，一个字都不要编。
5. **越界的话题不要主动抛**（见上面的信息边界）。**可以问、也应当问的**是：起降点、载重、货物类型、时间、机型、要不要重规划 —— 这些问完能直接干活。标准一句话：**问完能不能顺手做掉**。能就问，不能就别问。
6. **只做用户明确要求的，不要顺手多做一步。** 用户问「看看某地的湍流长什么样」是在提问，不是在改需求 —— 除了看图本身，**不许动起降点、途经点、时间、载重、机型这些规划输入**。这些是用户手上的工程，改错了要他重新收拾。曾经在这里翻过车：问「曲松县附近 40 公里 TKE 长什么样」，顺手就把终点改成了昌都。觉得「顺便把整条航线也算一下」更有帮助时，**先问一句再动**。（把用户正在问的那个图层打开给他看，不算多做 —— 那正是他在看的东西。）
7. **凡是在正文里写了带方位的区域判断，就要把同一批区域落到地图上。** 你写「西侧有一片高值区」「东北角平稳」「中心点最亮」，用户还得自己在脑子里对到地图上——**必须调 mark_regions 把它们画出来**，方框用 rect、成片区域用 rect、单个核心点用 pin。一次调用落全，label 用你正文里的方位词。这条不是「顺手多做」，这是把已经说出口的话落到图上，是第 6 条要求的完整交付。**但反过来：只做用户明确要求的，纯提问就不要去圈与问题无关的地方。**
8. 改了规划输入（起终点、途经点、时间、载重、机型）之后，如果用户想看结果，再调对应的 run_* 工具；不要凭改完的输入直接描述结果。
9. **用户明确说出来的每一个参数都必须先用工具设进去**（载重多少、几点起飞、什么机型、几个途经点），不能只在心里记下就当已经改好。参数没设就去规划，算出来的是另一组条件的结果。
10. **同一件事不要调两次。** 三档航线是一次规划同时算出来的，切换档位只调 set_priority 即可，**不要为了切档再调 run_plan**。
11. **正文里不要复述工具调用，也不要把工具名当术语抛给用户。** 界面上每一步操作都已单独列出（轨迹卡片），正文再说一遍「已调用 xxx」纯属重复；「用 set_priority 即可」这类话用户看不懂。要提档位就说「紧急档」，要操作就直接调工具。**更不要预告或复述工具的效果**——「我把这两块高值区标在地图上了」「已经帮你打开图层」这类句子是在你还看不到结果时就断言结果，写错了就是一句假话。做了就是做了，界面上会显示；没做就别说。
12. 回答用简洁中文，可用 Markdown 列表，控制在 300 字以内。不要输出思考过程、草稿或自我说明。"""


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
                slot = partial.setdefault(int(tc.get("index", 0)),
                                          {"id": "", "name": "", "args": ""})
                fn = tc.get("function") or {}
                # id 也要攒着：回灌工具结果时要用它配对 role=tool 消息，
                # 否则模型那边根本没有「这条结果对应哪次调用」的概念。
                if tc.get("id"):
                    slot["id"] = tc["id"]
                if fn.get("name"):
                    slot["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["args"] += fn["arguments"]
    for idx in sorted(partial):
        yield {"kind": "call", "id": partial[idx]["id"],
               "name": partial[idx]["name"], "args_raw": partial[idx]["args"]}


def _llm_stream(msgs: List[Dict[str, Any]]) -> Iterator[Dict[str, Any]]:
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
    # 走 _open_llm_stream 而不是 _open_stream：前者带 thinking 参数被拒时的重试兜底。
    # 这里原本直连 _open_stream，于是同一个网关，报告能发出去、对话却整轮挂掉。
    resp = assessor._open_llm_stream(body)
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

    「参数解析失败就丢掉」这条以前只是写在 docstring 里，代码却是 ``args = {}`` 照发 ——
    半截 JSON 于是变成一个**空参调用**。那个空参调用照样进轨迹、照样返回失败，
    而模型在同一轮里已经流式写下了「已经把区域标在地图上」。这里必须真的丢掉。
    """
    out: List[Dict[str, Any]] = []
    for ev in events:
        if ev.get("kind") != "call":
            continue
        name = (ev.get("name") or "").strip()
        if name not in tools.TOOL_NAMES:
            continue
        raw = (ev.get("args_raw") or "").strip()
        if raw:
            try:
                args = json.loads(raw)
            except Exception:
                continue
            if not isinstance(args, dict):
                continue
        else:
            # 真的没有参数字段：只有「声明了参数的工具」才算异常，无参工具本就传空
            args = {} if name in tools.PARAM_LESS_TOOLS else None
            if args is None:
                continue
        out.append({"id": ev.get("id") or "", "tool": name, "args": tools.validate_args(name, args)})
        if len(out) >= 6:
            break
    return out


def _build_messages(state: Dict[str, Any],
                    messages: List[Dict[str, Any]],
                    tool_results: List[Dict[str, Any]],
                    images: Optional[List[str]] = None,
                    said: str = "") -> List[Dict[str, Any]]:
    system = SYSTEM_PROMPT
    if state:
        system += "\n\n【当前界面状态】\n" + json.dumps(state, ensure_ascii=False)[:1200]

    out: List[Dict[str, Any]] = [{"role": "system", "content": system}]
    for m in (messages or [])[-MAX_MESSAGES:]:
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        out.append({
            "role": m["role"] if m.get("role") in ("user", "assistant") else "user",
            "content": content[:MAX_CHARS],
        })

    # 工具结果按**协议**回灌：assistant(tool_calls=[...]) + 一串 role=tool。
    #
    # 以前是把结果拼成一条条 `role:user` 的「【工具执行结果】{...}」发回去。模型分不清
    # 「这是用户说的话」和「这是喂给我的数据」，于是经常直接把这段 JSON 抄进正文 ——
    # 用户看到的就是它突然开始打印 `{"tool": "run_time_window", "ok": true...}`。
    # role=tool 是协议里专门放执行结果的位置，模型在这条消息上不会产生台词。
    pending = [tr for tr in (tool_results or []) if isinstance(tr, dict)]
    shots: List[Tuple[str, str]] = []          # (工具名, data URL)
    if pending:
        declared = [{
            "id": str(tr.get("id") or f"call_{n}"),
            "type": "function",
            "function": {
                "name": str(tr.get("tool") or ""),
                "arguments": json.dumps(tr.get("args") or {}, ensure_ascii=False),
            },
        } for n, tr in enumerate(pending)]
        said = str(said or "").strip()
        msg: Dict[str, Any] = {"role": "assistant", "tool_calls": declared}
        if said:
            # 模型这一轮自己说过的话也补回去。不补的话它下一轮不知道自己刚讲过什么，
            # 于是会把同一句解释换个说法再说一遍。
            msg["content"] = said
        out.append(msg)
        for tr in pending:
            data = tr.get("data")
            image = data.get("image") if isinstance(data, dict) else None
            if isinstance(image, str) and image.startswith("data:image/"):
                shots.append((str(tr.get("tool") or "工具"), image))
            out.append({
                "role": "tool",
                "tool_call_id": str(tr.get("id") or ""),
                "content": json.dumps({
                    "ok": tr.get("ok"),
                    "label": tr.get("label"),
                    "data": _strip_image(tr) if isinstance(data, dict) else data,
                }, ensure_ascii=False)[:1200],
            })

    # 工具结果里的图单独走一条 user 多模态消息：图有几十万字符，塞进 tool 消息既超
    # token 上限又毫无意义。放在 tool 消息**之后**是为了不打断 assistant→tool 的配对。
    for name, image in shots[:MAX_IMAGES]:
        out.append({
            "role": "user",
            "content": [
                {"type": "text", "text": f"（上面 {name} 那次工具调用返回的图）"},
                {"type": "image_url", "image_url": {"url": image, "detail": "high"}},
            ],
        })

    # 用户本次随消息发来的图：多模态 content 数组（图在前，文字在后）。
    # 过滤掉非 data:image 的串 —— 端点公网无鉴权，不能让它变成任意 URL 的抓取代理。
    seen_img = 0
    for url in (images or []):
        if seen_img >= MAX_IMAGES:
            break
        if isinstance(url, str) and url.startswith("data:image/") and len(url) <= MAX_IMAGE_CHARS:
            out.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": "（以上是用户本次发来的图片）"},
                    {"type": "image_url", "image_url": {"url": url}},
                ],
            })
            seen_img += 1
    return out


def _strip_image(tr: Dict[str, Any]) -> Dict[str, Any]:
    """把结果里的 data URL 换成占位说明。

    base64 图有几十万字符，塞进正文既超 token 上限又毫无意义 —— 图已经走
    image_url 通道单独送过去了。
    """
    data = tr.get("data")
    if not isinstance(data, dict) or "image" not in data:
        return tr
    shallow = dict(data)
    shallow["image"] = "<已随本消息附上该图>"
    return {**tr, "data": shallow}


def run_agent(state: Dict[str, Any], messages: List[Dict[str, Any]],
              tool_results: Optional[List[Dict[str, Any]]] = None,
              round_index: int = 0,
              images: Optional[List[str]] = None,
              said: str = "") -> Iterator[Dict[str, Any]]:
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

    msgs = _build_messages(state, messages, tool_results or [], images, said)

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
        # 不再报裸的异常类名：HTTPError 这种对用户毫无信息量，
        # 也看不出是「等一下就好」还是「额度用完了，等多久都没用」。
        yield {"type": "error", "message": assessor.describe_llm_error(exc)}
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
