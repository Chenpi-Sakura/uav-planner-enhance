"""AI 报告自检脚本：确认「思考输出」已关闭，并打印一份真实报告。

不依赖后端服务，直接按 .env / config.json 的配置打 OpenAI 兼容网关，用于回答两类问题：
  1. 网关是否接受 thinking={"type": "disabled"} 参数；
  2. 返回正文里是否还混着推理独白（reasoning 字段或内联思考标签）。

用法：
    cd backend
    .python/portable/python.exe scripts/check_ai_report.py

退出码：0 = 正文干净；1 = 配置缺失或调用失败；2 = 检测到思考内容残留（已兜底过滤）。
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# 强制 UTF-8 输出 (Windows cmd 默认 GBK, 打印中文/单位符号会 UnicodeEncodeError)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import assessor  # noqa: E402
from config import settings  # noqa: E402

# 一个足够真实的样例上下文（与前端可行性评估结果同构，字段做了精简）
SAMPLE_CTX = {
    "task": "feasibility",
    "feasible": True,
    "conclusion": "可飞",
    "priority": "normal",
    "origin": "拉萨市区附近",
    "destination": "林芝以北约21km",
    "distance": {"straight_km": 452.82, "route_km": 600.41,
                 "detour_ratio": 1.326, "flight_time_h": 10.01},
    "risk": {"overall": "中风险", "max_tke": 0.691, "max_edr": 0.0029,
             "moderate_ratio": 0.162, "n_samples": 120},
    "segments": [{"from": "拉萨市区附近", "to": "米拉山口", "level": "中风险",
                  "max_tke": 0.691, "distance_km": 168.4}],
    "blocking": {"regions": []},
    "warnings": [],
}

_REASONING_KEYS = ("reasoning", "reasoning_content", "reasoning_details", "thinking")
OPEN_TAG_HEAD = "<" + "think"


def mask_key() -> str:
    """打印 Key 时只留头尾，避免泄露。"""
    key = os.environ.get(settings.AI_API_KEY_ENV or "UAV_AI_API_KEY", "")
    if not key:
        return "(未设置)"
    return f"{key[:6]}...{key[-4:]} (共 {len(key)} 字符)"


def show_config() -> None:
    print("== 当前 AI 配置 ==")
    print("  AI_ENABLED        =", settings.AI_ENABLED)
    print("  AI_BASE_URL       =", settings.AI_BASE_URL or "(未设置)")
    print("  AI_MODEL          =", settings.AI_MODEL)
    print("  AI_THINKING       =", repr(settings.AI_THINKING))
    print("  AI_STRIP_THINKING =", settings.AI_STRIP_THINKING)
    print("  AI_MAX_TOKENS     =", settings.AI_MAX_TOKENS)
    print("  API Key 环境变量  =", settings.AI_API_KEY_ENV, "->", mask_key())


def open_stream(body):
    """发起流式请求；网关因 thinking 参数报错时去掉该字段再试一次。

    返回 (响应对象, thinking 是否被接受)。
    """
    try:
        return assessor._open_stream(body), True
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except OSError:
            pass
        print(f"  ! HTTP {exc.code}: {detail or exc.reason}")
        if "thinking" not in body or exc.code not in (400, 404, 422):
            raise
        print("  · 该网关不接受 thinking 参数，去掉后重试（改由响应侧兜底过滤）")
        body.pop("thinking", None)
        return assessor._open_stream(body), False


def diagnose(resp):
    """逐行消费 SSE，分别统计正文与思考内容。

    返回 (原始正文, reasoning 字段字符数, 内联思考标签命中数)。
    """
    parts = []
    reasoning_chars = 0
    tag_hits = 0
    for raw in resp:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            obj = json.loads(payload)
        except ValueError:
            continue
        choices = obj.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        for key in _REASONING_KEYS:
            value = delta.get(key)
            if isinstance(value, str):
                reasoning_chars += len(value)
            elif value:
                reasoning_chars += len(json.dumps(value, ensure_ascii=False))
        piece = delta.get("content") or ""
        tag_hits += piece.count(OPEN_TAG_HEAD)
        parts.append(piece)
    return "".join(parts), reasoning_chars, tag_hits


def main() -> int:
    show_config()
    if not settings.AI_ENABLED or not settings.AI_BASE_URL:
        print("")
        print("AI 未启用或未配置 base_url：报告走离线规则模板版。")
        print("请在仓库根目录 .env 中填写 UAV_AI_ENABLED / UAV_AI_BASE_URL / UAV_AI_MODEL / UAV_AI_API_KEY。")
        return 1
    body = assessor.build_request_body(SAMPLE_CTX)
    print("")
    print("== 请求参数 ==")
    print("  thinking =", body.get("thinking", "(不发送)"))
    print("")
    print("== 调用网关 ==")
    resp = None
    try:
        resp, accepted = open_stream(body)
    except Exception as exc:
        print(f"  x 调用失败: {type(exc).__name__}: {exc}")
        return 1
    try:
        raw_body, reasoning_chars, tag_hits = diagnose(resp)
    finally:
        resp.close()

    sanitizer = assessor.ThinkTagFilter()
    shown = sanitizer.feed(raw_body) + sanitizer.flush()
    print("")
    print("== 诊断结果 ==")
    print("  thinking 参数被网关接受 :", accepted)
    print("  reasoning 字段字符数    :", reasoning_chars)
    print("  正文内联思考标签命中    :", tag_hits)
    print("  原始正文字符数          :", len(raw_body))
    print("  兜底过滤后字符数        :", len(shown))
    print("")
    print("== 报告正文（过滤后）==")
    print(shown.strip() or "(空)")
    print("")
    if tag_hits or len(raw_body) != len(shown):
        print("! 检测到思考内容残留，已按兜底规则过滤后展示。")
        print("  如需彻底关闭，请确认模型支持 thinking=disabled，或在 .env 里设置 UAV_AI_THINKING=disabled。")
        return 2
    if reasoning_chars:
        print("OK：思考内容走的是 reasoning 独立字段，正文未受影响。")
    else:
        print("OK：正文干净，未检测到任何思考输出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
