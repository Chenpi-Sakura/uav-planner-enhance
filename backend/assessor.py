"""
AI 辅助评估（功能三）：把结构化的评估结果转成中文飞行建议。

两条路线（计划 §6.3）：
  A. 规则模板版（默认）—— 完全离线、确定性、可测试，由结构化结果直接渲染；
  B. LLM 版 —— OpenAI 兼容接口，支持流式输出；未配置或调用失败时自动降级到 A。

防幻觉约束（计划 §6.2）：
  - 报告正文只允许引用 build_context() 产出的 JSON 中的数据；
  - LLM 失败/超时/未配置一律降级为规则版，保证演示可用性。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Iterator, List, Optional

from config import settings

# ---------------------------------------------------------------- 上下文构建


def _fmt(v: Any, digits: int = 4, suffix: str = "") -> str:
    """把数值格式化为可读字符串；None/非数值返回「—」。"""
    if v is None:
        return "—"
    try:
        num = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{num:.{digits}f}{suffix}"


def build_feasibility_context(result: Dict[str, Any]) -> Dict[str, Any]:
    """从功能一结果中抽取报告需要的字段（不引入额外事实）。"""
    risk = result.get("risk") or {}
    stats = risk.get("stats") or {}
    distance = result.get("distance") or {}
    blocking = result.get("blocking") or {}
    regions = blocking.get("regions") or []
    segments = risk.get("segments") or []

    return {
        "task": "feasibility",
        "feasible": result.get("feasible"),
        "conclusion": result.get("conclusion"),
        "reason": result.get("reason"),
        "priority": result.get("priority"),
        "distance": {
            "straight_km": distance.get("straight_km"),
            "route_km": distance.get("route_km"),
            "detour_ratio": distance.get("detour_ratio"),
            "flight_time_h": distance.get("flight_time_h"),
            "time_cost_h": distance.get("time_cost_h") or {},
        },
        "risk": {
            "overall": risk.get("overall"),
            "max_tke": stats.get("max_tke"),
            "max_edr": stats.get("max_edr"),
            "moderate_ratio": stats.get("moderate_ratio"),
            "max_exposure_km": stats.get("max_exposure_km"),
            "n_samples": stats.get("n_samples"),
        } if risk else None,
        "segments": [
            {
                "from": s.get("from_name"),
                "to": s.get("to_name"),
                "level": s.get("level"),
                "max_tke": s.get("max_tke"),
                "distance_km": s.get("distance_km"),
            }
            for s in segments
        ],
        "blocking": [
            {
                "shape": r.get("shape"),
                "area_km2": round(float(r.get("area_km2") or 0.0), 1),
                "max_tke": r.get("max_tke"),
                "center": r.get("center"),
            }
            for r in regions
        ],
        "alt_bases": [
            {
                "role": a.get("role_label") or a.get("role"),
                "description": a.get("description"),
                "level": a.get("level"),
                "distance_km": a.get("distance_km"),
            }
            for a in ((result.get("suggestions") or {}).get("alt_bases") or [])
        ],
        "meta": result.get("meta") or {},
    }


def build_time_window_context(result: Dict[str, Any]) -> Dict[str, Any]:
    """从功能二结果中抽取报告需要的字段。"""
    rec = result.get("recommended") or {}
    forecast = result.get("forecast") or {}
    slots = result.get("slot_analysis") or []
    safe_slots = [s for s in slots if s.get("safe")]

    return {
        "task": "time_window",
        "date": result.get("date"),
        "has_window": result.get("has_window"),
        "reason": result.get("reason"),
        "recommended": {
            "takeoff": rec.get("takeoff"),
            "window": rec.get("window"),
            "landing": rec.get("landing"),
            "level": rec.get("level"),
            "max_tke": rec.get("max_tke"),
            "max_edr": rec.get("max_edr"),
        } if rec else None,
        "safe_slot_count": len(safe_slots),
        "safe_slots": [{"time": s.get("time"), "max_tke": s.get("max_tke")} for s in safe_slots],
        "backup_windows": result.get("backup_windows") or [],
        "warnings": [
            {"level": w.get("level"), "text": w.get("text")}
            for w in (result.get("warnings") or [])
        ],
        "forecast": {
            "enabled": forecast.get("enabled"),
            "first_available_date": forecast.get("first_available_date"),
            "days": [
                {
                    "date": d.get("date"),
                    "has_window": d.get("has_window"),
                    "best_window": d.get("best_window"),
                    "level": d.get("level"),
                    "safe_slot_count": d.get("safe_slot_count"),
                }
                for d in (forecast.get("days") or [])
            ],
        },
        "meta": result.get("meta") or {},
    }


# ---------------------------------------------------------------- 规则模板版


def render_rule_report(ctx: Dict[str, Any]) -> str:
    """离线规则模板版报告：确定性输出，LLM 不可用时兜底。"""
    if ctx.get("task") == "time_window":
        return _render_time_window(ctx)
    return _render_feasibility(ctx)


def _render_feasibility(ctx: Dict[str, Any]) -> str:
    lines: List[str] = []
    feasible = bool(ctx.get("feasible"))
    d = ctx.get("distance") or {}
    risk = ctx.get("risk") or {}
    segs = ctx.get("segments") or []
    blocking = ctx.get("blocking") or []
    alts = ctx.get("alt_bases") or []
    meta = ctx.get("meta") or {}
    eps = meta.get("endpoints") or {}

    # 1. 结论概述
    lines.append("## 结论概述")
    if feasible:
        lines.append(
            f"本次航线评估结论为「可飞」。全程风险等级为 {risk.get('overall') or '—'}，"
            f"航线总距离 {_fmt(d.get('route_km'), 2, ' km')}，"
            f"预计飞行 {_fmt(d.get('flight_time_h'), 2, ' h')}。"
        )
    else:
        reason_map = {
            "detour_exceeds_threshold": "存在安全航线但绕行成本超出当前优先级阈值",
            "no_safe_path": "航路被高强度湍流区封死",
            "data_coverage_insufficient": "起降点超出气象数据覆盖范围",
            "search_iteration_limit": "搜索达到迭代上限，未能确认安全航线",
        }
        lines.append(
            f"本次航线评估结论为「不可飞」。主要原因："
            f"{reason_map.get(str(ctx.get('reason')), '当前条件下不建议执行飞行任务')}。"
        )

    # 2. 航线地理叙事
    lines.append("")
    lines.append("## 航线地理叙事")
    start_desc = ((eps.get("start") or {}).get("describe")) or "起点"
    end_desc = ((eps.get("end") or {}).get("describe")) or "终点"
    lines.append(f"航线由 {start_desc} 飞往 {end_desc}，直线距离 {_fmt(d.get('straight_km'), 2, ' km')}。")
    if segs:
        seg_txt = "；".join(
            f"{s.get('from')}→{s.get('to')}（{s.get('level')}，{_fmt(s.get('distance_km'), 1, ' km')}）"
            for s in segs
        )
        lines.append(f"按途经城镇切分，各段风险依次为：{seg_txt}。")

    # 3. 气象解读
    lines.append("")
    lines.append("## 气象解读")
    if risk:
        lines.append(
            f"沿航线采样 {risk.get('n_samples') or '—'} 个格点，"
            f"最大 TKE {_fmt(risk.get('max_tke'))}、最大 EDR {_fmt(risk.get('max_edr'))}，"
            f"中度以上风险格点占比 {_fmt((risk.get('moderate_ratio') or 0) * 100, 1, '%')}。"
        )
        worst = max(segs, key=lambda s: float(s.get("max_tke") or 0), default=None) if segs else None
        if worst:
            lines.append(
                f"湍流最强区段出现在 {worst.get('from')}→{worst.get('to')}，"
                f"该段最大 TKE {_fmt(worst.get('max_tke'))}。"
            )
    if blocking:
        b = blocking[0]
        lines.append(
            f"检测到 {len(blocking)} 处高危湍流区，其中最主要的一处为 {b.get('shape')}，"
            f"面积约 {_fmt(b.get('area_km2'), 1, ' km²')}，峰值 TKE {_fmt(b.get('max_tke'))}。"
        )

    # 4. 时间建议
    lines.append("")
    lines.append("## 时间建议")
    tc = d.get("time_cost_h") or {}
    if tc.get("applicable"):
        lines.append(
            f"绕行额外耗时 {_fmt(tc.get('extra'), 2, ' h')}，"
            f"当前优先级允许上限 {_fmt(tc.get('limit'), 2, ' h')}，"
            f"{'已超出该上限' if tc.get('exceeded') else '在该上限之内'}。"
        )
    else:
        lines.append("未规划出安全航线，绕行成本无法计算。")
    lines.append("可切换到「时间窗口」标签页，扫描当日及后续日期的更优起飞时段。")

    # 5. 操作建议
    lines.append("")
    lines.append("## 操作建议")
    if feasible:
        lines.append("当前条件下可执行任务，建议按计划起飞并保持对湍流高发区段的关注。")
    else:
        lines.append("建议调整起降点、更换飞行时间，或推迟任务等待更优气象条件。")
    if alts:
        alt_txt = "；".join(
            f"{a.get('role')}→{a.get('description')}（{a.get('level')}，{_fmt(a.get('distance_km'), 1, ' km')}）"
            for a in alts
        )
        lines.append(f"可用替代起降点：{alt_txt}。")

    # 6. 数据口径声明
    lines.append("")
    lines.append("## 数据口径声明")
    lines.append(
        "本报告基于 500hPa 等压面气象数据，空间分辨率约 5km、时间分辨率逐小时；"
        "TKE / EDR 为基于三维风场的代理估算值，不构成真实气象观测。"
    )
    return "\n".join(lines)


def _render_time_window(ctx: Dict[str, Any]) -> str:
    lines: List[str] = []
    rec = ctx.get("recommended") or {}
    forecast = ctx.get("forecast") or {}
    warnings = ctx.get("warnings") or []
    backup = ctx.get("backup_windows") or []
    meta = ctx.get("meta") or {}

    lines.append("## 结论概述")
    if ctx.get("has_window") and rec:
        lines.append(
            f"{ctx.get('date')} 存在安全飞行时间窗口，推荐 {rec.get('takeoff')} 起飞，"
            f"飞行时段 {rec.get('window', ['—', '—'])[0]} – {rec.get('window', ['—', '—'])[1]}，"
            f"预计 {rec.get('landing')} 降落，全程风险等级 {rec.get('level')}。"
        )
    else:
        lines.append(
            f"{ctx.get('date')} 未找到全程安全的飞行时间窗口，"
            f"扫描共命中 {ctx.get('safe_slot_count') or 0} 个安全时段。"
        )

    lines.append("")
    lines.append("气象解读")
    if rec:
        lines.append(
            f"推荐时段内最大 TKE {_fmt(rec.get('max_tke'))}、"
            f"最大 EDR {_fmt(rec.get('max_edr'))}，"
            f"按 1 小时步长完成 {24} 个时次扫描。"
        )
    lines.append(
        f"数据时区口径：输入为北京时间，数据标签为 UTC"
        f"（偏移 {meta.get('tz_offset_hours', 8)} 小时），已在后端完成换算。"
    )

    lines.append("")
    lines.append("时间建议")
    if backup:
        lines.append("本日其他可用时段：")
        for w in backup:
            lines.append(
                f"- {w.get('start')} – {w.get('end')}（{w.get('level')}，"
                f"最大 TKE {_fmt(w.get('max_tke'))}）"
            )
    else:
        lines.append("本日无其他可用的备用时段。")
    if forecast.get("enabled") and forecast.get("days"):
        lines.append("后续日期推演结果：")
        for d in forecast["days"]:
            if d.get("has_window"):
                bw = d.get("best_window") or []
                lines.append(
                    f"- {d.get('date')}：有安全时段"
                    f"{f'（{bw[0]} – {bw[1]}）' if len(bw) >= 2 else ''}，等级 {d.get('level')}"
                )
        first = forecast.get("first_available_date")
        if first:
            lines.append(f"首个可飞行日期为 {first}。")

    lines.append("")
    lines.append("操作建议")
    if ctx.get("has_window"):
        lines.append("建议按推荐时刻起飞，并预留约 30 分钟机动时间以应对临近时段的气象变化。")
    else:
        lines.append("建议推迟任务、更换起降点，或等待后续推演给出的可用日期。")
    if warnings:
        lines.append("其他预警提示：")
        for w in warnings:
            lines.append(f"- {w.get('text')}")

    lines.append("")
    lines.append("数据口径声明")
    lines.append(
        "本报告基于 500hPa 等压面气象数据，空间分辨率约 5km、时间分辨率逐小时；"
        "「7 天推演」为基于现有数据集的向前推演，非真实气象预报。"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------- LLM 版


def llm_available() -> bool:
    """是否具备调用 LLM 的条件（开关打开 + 配置了 base_url）。"""
    return bool(settings.AI_ENABLED and settings.AI_BASE_URL)


def build_prompt(ctx: Dict[str, Any]) -> str:
    """把结构化上下文包成单条 user prompt（防幻觉的关键：只喂结构化 JSON）。"""
    task_desc = (
        "请基于以下飞行路径可行性评估结果，生成中文飞行建议报告。"
        if ctx.get("task") == "feasibility"
        else "请基于以下飞行时间窗口扫描结果，生成中文飞行建议报告。"
    )
    return (
        f"{task_desc}\n\n"
        f"```json\n{json.dumps(ctx, ensure_ascii=False, indent=2)}\n```\n\n"
        "要求：只引用上面 JSON 中出现的数值与地名；按「结论概述 / 航线地理叙事 / "
        "气象解读 / 时间建议 / 操作建议 / 数据口径声明」分段作答。"
    )


def _iter_sse_content(resp) -> Iterator[str]:
    """解析 OpenAI 兼容的 SSE 流，逐段产出文本增量。"""
    for raw_line in resp:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line or not line.startswith("data:"):
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
        piece = delta.get("content")
        if piece:
            yield piece


def stream_llm_report(ctx: Dict[str, Any]) -> Iterator[str]:
    """以流式方式调用 OpenAI 兼容的 /chat/completions，逐段产出文本。

    使用标准库 urllib（项目便携 Python 未安装 httpx/requests），
    因此这里以 ``readline`` 方式逐行消费响应，实现真实的流式输出。
    """
    url = f"{settings.AI_BASE_URL}/chat/completions"
    body = {
        "model": settings.AI_MODEL,
        "messages": [
            {"role": "system", "content": settings.AI_SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(ctx)},
        ],
        "temperature": settings.AI_TEMPERATURE,
        "max_tokens": settings.AI_MAX_TOKENS,
        "stream": True,
    }
    api_key = os.environ.get(settings.AI_API_KEY_ENV or "UAV_AI_API_KEY", "")
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            **({"Authorization": f"Bearer {api_key}"} if api_key else {}),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=settings.AI_TIMEOUT_S) as resp:
        yield from _iter_sse_content(resp)


def generate_report_stream(ctx: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """统一入口：产出 SSE 事件字典。

    事件类型：
      - ``{"type": "meta", "source": "llm"|"rule"}``：报告来源，前端可据此提示；
      - ``{"type": "delta", "text": "..."}``：正文增量；
      - ``{"type": "error", "message": "..."}``：LLM 失败，随后自动降级为规则版；
      - ``{"type": "done"}``：结束。
    """
    if llm_available():
        try:
            yield {"type": "meta", "source": "llm"}
            produced = False
            for piece in stream_llm_report(ctx):
                produced = True
                yield {"type": "delta", "text": piece}
            if produced:
                yield {"type": "done"}
                return
            # 没有任何增量（例如网关不支持流式）→ 明确降级
            yield {
                "type": "error",
                "message": "LLM 未返回任何内容，已回退到规则模板版报告。",
            }
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
            yield {
                "type": "error",
                "message": f"LLM 调用失败（{type(exc).__name__}），已回退到规则模板版报告。",
            }

    # 规则模板版（默认 / 降级路径）：一次性给出全部文本
    yield {"type": "meta", "source": "rule"}
    yield {"type": "delta", "text": render_rule_report(ctx)}
    yield {"type": "done"}


def report_available() -> Dict[str, Any]:
    """给前端的可用性探测信息（不泄露 API Key）。"""
    return {
        "llm_enabled": bool(settings.AI_ENABLED),
        "llm_ready": llm_available(),
        "model": settings.AI_MODEL if settings.AI_ENABLED else None,
        "base_url_configured": bool(settings.AI_BASE_URL),
    }
