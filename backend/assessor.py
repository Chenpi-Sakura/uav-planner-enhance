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


# 报告正文里的湍流量一律带单位：换了口径之后，裸数字无法区分是旧的
# 「平均风动能」还是新的「脉动方差」。
TKE_UNIT = " m²/s²"
# EDR 的指数是分数 2/3，Unicode 只有 ⁰¹²³⁴⁵⁶⁷⁸⁹ 和 ⁄，没有对应的上标码位，
# 只能退而求其次拼成 ASCII 的 m^(2/3)/s —— 那玩意儿印在正文里像乱码。
# 前端接了 KaTeX，这里直接给行内 LaTeX，让它去排版。
EDR_UNIT = " $\\mathrm{m}^{2/3}/\\mathrm{s}$"

# 两条报告（可行性 / 时间窗口）共用的湍流量口径。写两遍必然漂移，
# 而这里的数字要和前端 config.ts 的 TURB 保持一致。
# 这段会进 prompt，所以公式用 LaTeX 写：模型照抄进正文就能直接渲染，
# 省得它把 $\mathrm{m}^{2/3}/\mathrm{s}$ 又「翻译」回 m^(2/3)/s。
TURB_NOTE = (
    f"TKE 为同层 u/v/w 三分量在 ±{int(settings.TURB_WINDOW_HALF_HOURS)} 小时窗口内的"
    "脉动方差（单位 m²/s²），EDR 由 $\\varepsilon = C\\cdot k^{3/2}/L$ 换算、取 L = "
    f"{int(settings.TURB_EDR_LENGTH_SCALE_M)} m（单位 $\\mathrm{{m}}^{{2/3}}/\\mathrm{{s}}$）"
)


# 上下文里对报告无用、却会诱导模型自行换算时区的内部标志。
_LLM_META_DROP = ("data_label_is_utc",)

# 正文时间已在后端换算完毕；不显式声明的话，模型会拿 meta 里的偏移量再算一次，
# 产出与面板矛盾的起飞时间。
TIME_BASE_NOTE = (
    "本报告涉及的时间均为北京时间（UTC+8），后端已完成时区换算；"
    "请直接引用，禁止再加减时区偏移或标注为 UTC。"
)


def _llm_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """透出给报告的元信息：保留阈值等展示字段，剔除内部时区标志。"""
    return {k: v for k, v in (meta or {}).items() if k not in _LLM_META_DROP}


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
        "verdict": result.get("verdict"),
        "reason": result.get("reason"),
        "priority": result.get("priority"),
        # 「没算完」与「真没路」的区分依据，只透传不换算
        "search_effort": result.get("search_effort"),
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
        "meta": _llm_meta(result.get("meta")),
        "time_base": TIME_BASE_NOTE,
        "turbulence_calibration": TURB_NOTE,
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
        "meta": _llm_meta(result.get("meta")),
        "time_base": TIME_BASE_NOTE,
        "turbulence_calibration": TURB_NOTE,
    }


def build_weather_context(result: Dict[str, Any]) -> Dict[str, Any]:
    """把气象实况取数结果（``weather_query.query_weather``）包成对话上下文。

    存在的意义是给「现在那边 TKE 是多少」这类问题一个**真数**可引。
    既有两条上下文里只有评估结论，模型手里没有原始读数，只能拿「数据显示」搪塞。

    ``available=False`` 的点原样带过去并标明原因：让模型如实说查不到，
    而不是去别处编一个数 —— 防幻觉纪律在这里最容易破，因为它看起来只是「回答问题」。
    """
    points = []
    for p in (result.get("points") or []):
        row = {
            "name": p.get("name"),
            "available": bool(p.get("available")),
        }
        if p.get("available"):
            row.update({
                "tke": p.get("tke"),
                "edr": p.get("edr"),
                "level": p.get("level"),
                "wind_speed_ms": p.get("wind_speed_ms"),
                "temp_c": p.get("temp_c"),
                "humidity_pct": p.get("rh_pct"),
            })
        else:
            row["reason"] = p.get("note")
        points.append(row)

    return {
        "task": "weather",
        "available": bool(result.get("available")),
        # 顶层失败的原因必须透传：只给 available=false 而不给原因，模型只能
        # 答一句「查不到」，用户分不清是没这个地点、数据没加载还是那一刻缺测。
        "reason": result.get("reason"),
        "query_time": result.get("query_time"),
        "timezone": result.get("timezone"),
        "level_hpa": result.get("level_hpa"),
        "level_note": result.get("level_note"),
        "points": points,
        "thresholds": result.get("thresholds"),
        # 用户很可能接着问「TKE 是什么」，把口径一并给出去，省得模型自己编解释。
        # 公式里的系数取自 settings，改阈值时不会与这里说的口径脱钩。
        "field_glossary": {
            "tke": "湍流动能，数值越大说明该处大气扰动越强，单位 m²/s²",
            "edr": "等效顺变率（阵风强度），反映湍流对飞行器结构载荷的等效强度。"
                    f"由 TKE 经外尺度换算：EDR = ε^(1/3)，其中 ε = {settings.TURB_EDR_C_EPS}·k^1.5/L，"
                    "故单位为 $\\mathrm{{m}}^{{2/3}}/\\mathrm{{s}}$（不是无量纲）；数值越大对结构载荷越不利",
            "wind_speed_ms": "水平风速模长 |V| = sqrt(u² + v²)，单位 m/s",
            "temp_c": "气温。注意这是数据所在高度层（见 level_note，约 5 km）的温度，"
                       "**不是地面气温**，回答时必须说清高度层，不要说成当地地面温度",
            "humidity_pct": "相对湿度，单位 %",
            "level": "按 TKE 与 EDR 取较高的一档：低风险 / 中风险 / 高风险 / 不可飞",
            "unavailable": "available 为 false 的点表示那一刻没有数据（未读到或缺测），"
                           "绝不能当成 0 或「风平浪静」来回答",
        },
        "turbulence_calibration": TURB_NOTE,
        "time_base": TIME_BASE_NOTE,
    }


# ---------------------------------------------------------------- 规则模板版


def render_rule_report(ctx: Dict[str, Any]) -> str:
    """离线规则模板版报告：确定性输出，LLM 不可用时兜底。"""
    if ctx.get("task") == "time_window":
        return _render_time_window(ctx)
    return _render_feasibility(ctx)


# 四态里最容易被读反的两种：报告必须自己说清性质，否则「超预算/未算出」
# 会被用户当成「飞不了」，进而以为越保守的档越危险。
VERDICT_ADVICE = {
    "over_budget": (
        "本次结论是「不满足该档时间预算」，安全航线本身已经找到："
        "可改选绕行上限更大的档位、更换起飞时刻，或直接接受这份额外耗时。"
    ),
    "undetermined": (
        "本次结论只是「搜索未收敛」，不能据此判定没有安全航线："
        "可缩短航程或减少途经点、放宽湍流阈值，或稍后重试。"
    ),
}


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
            "search_iteration_limit": "搜索未收敛（展开预算或时间上限先到），未能确认安全航线",
        }
        lines.append(
            f"本次航线评估结论为「{ctx.get('conclusion') or '不可飞'}」。主要原因："
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
        worst = max(segs, key=lambda s: float(s.get("max_tke") or 0), default=None) if segs else None
        # 先给判断再给数：读者要的是「湍流大不大」，不是一份采样统计
        ratio = (risk.get("moderate_ratio") or 0) * 100
        lines.append(
            "沿线湍流整体"
            + ("偏强" if ratio >= 10 or (risk.get("max_tke") or 0) >= 16 else "可控")
            + "，没有成片的高危区。"
        )
        lines.append(
            f"全程最大 TKE {_fmt(risk.get('max_tke'), 2, TKE_UNIT)}、"
            f"最大 EDR {_fmt(risk.get('max_edr'), 3, EDR_UNIT)}，"
            f"中度以上风险格点占比 {_fmt(ratio, 1, '%')}。"
        )
        if worst:
            lines.append(
                f"湍流最强区段出现在 {worst.get('from')}→{worst.get('to')}，"
                f"该段最大 TKE {_fmt(worst.get('max_tke'), 2, TKE_UNIT)}。"
            )
    if blocking:
        b = blocking[0]
        lines.append(
            f"检测到 {len(blocking)} 处高危湍流区，其中最主要的一处为 {b.get('shape')}，"
            f"面积约 {_fmt(b.get('area_km2'), 1, ' km²')}，"
            f"峰值 TKE {_fmt(b.get('max_tke'), 2, TKE_UNIT)}。"
        )

    # 4. 时间建议
    lines.append("")
    lines.append("## 时间建议")
    tc = d.get("time_cost_h") or {}
    if tc.get("applicable"):
        # 结论在前：超没超是用户要的那句话，两个数只是佐证
        lines.append(
            ("绕行代价超出当前优先级允许的上限，"
             if tc.get("exceeded") else "绕行代价在当前优先级允许的上限之内，")
            + f"额外耗时 {_fmt(tc.get('extra'), 2, ' h')}，上限 {_fmt(tc.get('limit'), 2, ' h')}。"
        )
    else:
        lines.append("本档没有得出可展示的航线，绕行成本无法计算。")
    lines.append("可切换到「时间窗口」标签页，扫描当日及后续日期的更优起飞时段。")

    # 5. 操作建议
    lines.append("")
    lines.append("## 操作建议")
    if feasible:
        lines.append("当前条件下可执行任务，建议按计划起飞并保持对湍流高发区段的关注。")
    else:
        eff = ctx.get("search_effort") or {}
        if str(ctx.get("verdict")) == "undetermined" and eff.get("budget"):
            e, b = int(eff["expansions"]), int(eff["budget"])
            lines.append(
                f"本次搜索研究过 {e} 个路口，用满了 {b} 次展开预算。" if e >= b else
                f"本次搜索研究过 {e} 个路口（{b} 次预算并未用满），"
                f"到了 {float(eff.get('elapsed_s') or settings.ASTAR_TIME_LIMIT_S):.0f} 秒的时间上限。")
        lines.append(VERDICT_ADVICE.get(
            str(ctx.get("verdict")),
            "建议调整起降点、更换飞行时间，或推迟任务等待更优气象条件。",
        ))
    if alts:
        alt_txt = "；".join(
            f"{a.get('role')}→{a.get('description')}（{a.get('level')}，{_fmt(a.get('distance_km'), 1, ' km')}）"
            for a in alts
        )
        lines.append(f"可用替代起降点：{alt_txt}。")
    return "\n".join(lines)


def _render_time_window(ctx: Dict[str, Any]) -> str:
    lines: List[str] = []
    rec = ctx.get("recommended") or {}
    forecast = ctx.get("forecast") or {}
    warnings = ctx.get("warnings") or []
    backup = ctx.get("backup_windows") or []

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
            f"推荐时段内湍流偏弱，最大 TKE {_fmt(rec.get('max_tke'), 2, TKE_UNIT)}、"
            f"最大 EDR {_fmt(rec.get('max_edr'), 3, EDR_UNIT)}。"
        )

    lines.append("")
    lines.append("时间建议")
    if backup:
        lines.append("本日其他可用时段：")
        for w in backup:
            lines.append(
                f"- {w.get('start')} – {w.get('end')}（{w.get('level')}，"
                f"最大 TKE {_fmt(w.get('max_tke'), 2, TKE_UNIT)}）"
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
    # 这几条是踩过坑之后加的，不是泛泛的「不要编造」：
    #   「墨竹工卡」被写成「墨竹墨卡」——地名按语义脑补了同音字；
    #   「备用机场清单为空」——JSON 里根本没有这个字段，模型自己补了一个空清单出来；
    #   正文里冒出 `blocking`、`alt_bases` 这类键名——把接口结构当成了话说给用户听；
    #   结论概述变成一串数字——用户要的是「能不能飞」，不是一份数据表。
    # 都不是数值幻觉，靠「只引用 JSON 中的数值」这句盖不住，得逐条点名。
    return (
        f"{task_desc}\n\n"
        f"```json\n{json.dumps(ctx, ensure_ascii=False, indent=2)}\n```\n\n"
        "要求：\n"
        "1. **给结论，不给数据。** 每一节先说结论，再在必要处补一两个支撑数值。"
        "用户要知道的是「能不能飞、有什么风险、该怎么飞」，不是来查字段的。"
        "写成「全线可飞，风险低，按计划起飞即可」是对的；"
        "写成「直线距离 312.11 km，航线距离 312.12 km，绕航比 1，巡航速度 60 km/h」是不对的。\n"
        "2. 地名、机场名、航路点名称必须**逐字照抄** JSON 中的写法，"
        "不得改写、简写、换字或按语义补全；拿不准就原样复制，不要自己拼。\n"
        "3. 正文里**不得出现 JSON 的字段名或键名**（如 blocking、alt_bases、max_tke、"
        "flight_time_h、overall、segments），一律改写成中文说法"
        "（「无阻塞区」「无备用起降点」「湍流动能最大值为 …」）。\n"
        "4. 只能引用 JSON 中**实际存在**的字段。JSON 里没有的字段"
        "（备用机场清单、跑道长度、机型性能、电池续航、空域批准情况等）**一律不得提及**——"
        "既不能报它的值，也不能说它为空、缺失或「无需」。没有数据就不写那一项。\n"
        "5. 数值连同单位照抄，不要四舍五入、换算或补充「约」「以上」等修饰；"
        "并注意别串口径——「±6 小时窗口」这类只属于湍流方差计算，"
        "不能当成飞行时长或可用时间窗口的取值范围。\n"
        "6. 上下文里写成 `$\\mathrm{m}^{2/3}/\\mathrm{s}$` 这类行内 LaTeX 的单位与公式，"
        "**连着 `$` 一起原样照抄**，前端会渲染成真正的上下标；"
        "不要「翻译」回 `m^(2/3)/s` 这种 ASCII 写法。需要自己写公式时也一律用 `$...$` 包裹。\n"
        "7. 按「结论概述 / 航线地理叙事 / 气象解读 / 时间建议 / 操作建议」"
        "五节分段作答，每节都要有。不要输出「数据口径声明」这类元信息小节。"
    )


# 部分推理型模型会把思考过程直接写进正文，
# 常见形态是用内联标签包裹。这里只按已知标签名成对剥离；标签不在名单内时
# 原样输出，宁可少删也不破坏正文。
_THINK_TAGS = tuple(
    (f"<{name}>", f"</{name}>")
    for name in ("think", "thinking", "thought", "reasoning", "REASONING_SCRATCHPAD")
)


class ThinkTagFilter:
    """跨增量剥离内联思考标签。

    SSE 分块可能把标签切断（例如上一块以 ``<thi`` 结尾），因此对可能是
    标签前缀的尾巴做缓冲，等下一块到齐再判断。
    """

    def __init__(self) -> None:
        self._buf = ""
        self._closing = ""  # 非空表示当前正处于某个思考块内部

    @staticmethod
    def _find_open(buf: str):
        """返回最早出现的开标签位置及其配对闭标签。"""
        best = None
        for open_tag, close_tag in _THINK_TAGS:
            idx = buf.find(open_tag)
            if idx >= 0 and (best is None or idx < best[0]):
                best = (idx, close_tag)
        return best

    @staticmethod
    def _hold_start(buf: str) -> int:
        """给出末尾「可能是标签前缀」的起始下标；无嫌疑时返回 len(buf)。"""
        longest = max(len(tag) for pair in _THINK_TAGS for tag in pair)
        for size in range(min(len(buf), longest - 1), 0, -1):
            tail = buf[-size:]
            if any(tag.startswith(tail) for pair in _THINK_TAGS for tag in pair):
                return len(buf) - size
        return len(buf)

    def feed(self, text: str) -> str:
        """喂入一段原始增量，返回可以展示给用户的部分。"""
        self._buf += text
        out: List[str] = []
        while True:
            if self._closing:
                idx = self._buf.find(self._closing)
                if idx < 0:
                    self._buf = self._buf[self._hold_start(self._buf):]
                    break
                self._buf = self._buf[idx + len(self._closing):]
                self._closing = ""
                continue
            hit = self._find_open(self._buf)
            if hit is None:
                keep = self._hold_start(self._buf)
                out.append(self._buf[:keep])
                self._buf = self._buf[keep:]
                break
            start, close_tag = hit
            out.append(self._buf[:start])
            self._closing = close_tag
            self._buf = self._buf[start:]
        return "".join(out)

    def flush(self) -> str:
        """流结束时取出剩余内容；未闭合的思考块视为残稿，整段丢弃。"""
        if self._closing:
            self._buf = ""
            return ""
        out, self._buf = self._buf, ""
        return out


def _iter_sse_content(resp) -> Iterator[str]:
    """解析 OpenAI 兼容的 SSE 流，逐段产出**正文**增量。

    只取 ``delta.content``：推理型网关会把思考过程放在 reasoning /
    reasoning_content / reasoning_details 等独立字段里，这里天然不会读到；
    ``AI_STRIP_THINKING`` 打开时再叠加一层内联标签剥离，
    避免「思考独白混进报告正文」这种观感很差的情况。
    """
    sanitizer = ThinkTagFilter() if settings.AI_STRIP_THINKING else None
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
        if not piece:
            continue
        if sanitizer is None:
            yield piece
            continue
        shown = sanitizer.feed(piece)
        if shown:
            yield shown
    if sanitizer is not None:
        tail = sanitizer.flush()
        if tail:
            yield tail


def chat_request_body(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """组装 /chat/completions 请求体（报告与对话共用）。

    关键是显式关闭推理型模型的「思考输出」：这类模型默认会把
    思考过程一并写进返回，前端就会把推理独白当成正文展示。
    ``AI_THINKING`` 为空或取值不认识时不发送该字段，以兼容不支持它的网关
    （部分自建/本地部署的推理服务），此时改由响应侧的 ThinkTagFilter 兜底。
    """
    body: Dict[str, Any] = {
        "model": settings.AI_MODEL,
        "messages": messages,
        "temperature": settings.AI_TEMPERATURE,
        "max_tokens": settings.AI_MAX_TOKENS,
        "stream": True,
    }
    thinking = (settings.AI_THINKING or "").strip().lower()
    if thinking in ("disabled", "adaptive"):
        body["thinking"] = {"type": thinking}
    return body


def report_messages(ctx: Dict[str, Any]) -> List[Dict[str, str]]:
    """报告的单轮消息：系统口径 + 一段喂进去的结构化 JSON。"""
    return [
        {"role": "system", "content": settings.AI_SYSTEM_PROMPT},
        {"role": "user", "content": build_prompt(ctx)},
    ]


def build_request_body(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """报告的单轮请求体（单独成函数便于测试）。"""
    return chat_request_body(report_messages(ctx))


def _open_stream(body: Dict[str, Any]) -> Any:
    """发起流式请求并返回响应对象（调用方负责关闭）。"""
    url = f"{settings.AI_BASE_URL}/chat/completions"
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
    return urllib.request.urlopen(req, timeout=settings.AI_TIMEOUT_S)


def _open_llm_stream(body: Dict[str, Any]) -> Any:
    """发起流式请求并返回响应对象（调用方负责关闭）。"""
    try:
        return _open_stream(body)
    except urllib.error.HTTPError as exc:
        # 个别网关不认识 thinking 参数会直接 4xx：去掉该字段重试一次，
        # 思考内容交给响应侧过滤器兜底，避免整个请求退化成规则版。
        if "thinking" not in body or exc.code not in (400, 404, 422):
            raise
        body.pop("thinking", None)
        return _open_stream(body)


def describe_llm_error(exc: BaseException) -> str:
    """把调用异常翻译成用户看得懂、且知道下一步做什么的一句话。

    之前这里直接报 ``HTTPError``：用户看到「AI 服务调用失败（HTTPError）」既不知道
    出了什么事，也不知道该等还是该充钱。实测最常撞上的是 429 —— 网关侧的
    「Token Plan 用量上限」是个**硬上限**不是瞬时限流，重试多少次都一样，
    所以必须原样说清楚，不能包装成「稍后重试」。
    """
    if isinstance(exc, urllib.error.HTTPError):
        code = exc.code
        detail = ""
        try:
            raw = exc.read().decode("utf-8", "replace")
            detail = json.loads(raw).get("error", {}).get("message", "")
        except Exception:
            detail = ""
        if code == 429:
            return ("AI 服务用量已达上限"
                    + (f"（{detail}）" if detail else "")
                    + "。这不是故障，是额度用完了，充值或等计费周期重置后才能继续。")
        if code in (401, 403):
            return "AI 服务鉴权失败，请检查 API Key 配置。"
        if code == 404:
            return f"AI 服务接口不存在（{exc.url}），请检查 AI_BASE_URL 与模型名配置。"
        if code in (400, 422):
            return f"AI 服务拒绝了本次请求（{code}）" + (f"：{detail}" if detail else "。")
        return f"AI 服务返回 HTTP {code}" + (f"：{detail}" if detail else "。")
    return f"AI 服务调用失败（{type(exc).__name__}）：{exc}"


def stream_llm_messages(messages: List[Dict[str, str]]) -> Iterator[str]:
    """以流式方式调用 OpenAI 兼容的 /chat/completions，逐段产出正文增量。

    使用标准库 urllib（项目便携 Python 未安装 httpx/requests），
    因此这里以 ``readline`` 方式逐行消费响应，实现真实的流式输出。
    """
    with _open_llm_stream(chat_request_body(messages)) as resp:
        yield from _iter_sse_content(resp)


def stream_llm_report(ctx: Dict[str, Any]) -> Iterator[str]:
    """报告口径的流式正文（等价于 ``stream_llm_messages(report_messages(ctx))``）。"""
    return stream_llm_messages(report_messages(ctx))


def generate_report_stream(ctx: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """统一入口：产出 SSE 事件字典。

    事件类型：
      - ``{"type": "meta", "source": "llm"|"rule"}``：报告来源，前端可据此提示；
      - ``{"type": "delta", "text": "..."}``：正文增量；
      - ``{"type": "error", "message": "..."}``：LLM 失败，随后自动降级为规则版；
      - ``{"type": "done"}``：结束。
    """
    if llm_available():
        # 「声明来源」必须晚于「确认真拿到内容」：
        # 若先发 meta:llm 再失败，前端来源标签会先闪出「23:04」再跳成
        # 「规则兜底」，看起来像是重复展示。因此这里等首个增量到达后
        # 才补发 meta，保证来源标签只出现一次且终态正确。
        announced = False
        try:
            for piece in stream_llm_report(ctx):
                if not announced:
                    yield {"type": "meta", "source": "llm"}
                    announced = True
                yield {"type": "delta", "text": piece}
            if announced:
                yield {"type": "done"}
                return
            # 没有任何增量（例如网关不支持流式）→ 明确降级
            yield {
                "type": "error",
                "message": "LLM 未返回任何内容，已回退到规则模板版报告。",
            }
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
            # 已经吐出过部分正文时不能再降级，否则规则版会与残稿叠在一起。
            if announced:
                yield {
                    "type": "error",
                    "message": f"LLM 输出中断（{type(exc).__name__}），以上为已生成的部分内容。",
                }
                yield {"type": "done"}
                return
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


# ---------------------------------------------------------------- 多轮对话

# 这个端点在公网无鉴权（见 AGENTS.md），所以历史条数与单条字数都要封顶：
# 不设上限时，一次请求就能把上下文灌到网关的 token 上限，既慢又贵。
CHAT_MAX_MESSAGES = 12
CHAT_MAX_CHARS = 1500

CHAT_SYSTEM_PROMPT = (
    '你是无人机气象航线决策助手。用户会就下方 JSON 里的评估结果追问，'
    '只允许引用 JSON 中出现的数值与地名，禁止编造任何气象事实；'
    '不确定时使用「数据显示」措辞。JSON 中的时间已是北京时间（UTC+8），'
    '照抄即可，禁止再做时区换算或标注 UTC。'
    '用简洁中文作答，可用 Markdown 列表，控制在 300 字以内；'
    '不要输出思考过程、草稿或自我说明，不要复述输入原文。'
)


def build_chat_messages(ctx: Dict[str, Any], history: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """系统口径 + 当前评估结果 + 最近若干轮对话。

    上下文只来源于前端送上来的结构化结果（与报告同一防幻觉口径），
    后端不额外取数。历史超出 ``CHAT_MAX_MESSAGES`` 时保留最新的尾部。
    """
    system = (
        f"{CHAT_SYSTEM_PROMPT}\n\n"
        f"【当前评估结果】\n```json\n{json.dumps(ctx, ensure_ascii=False)}\n```"
    )
    messages: List[Dict[str, str]] = [{"role": "system", "content": system}]
    for msg in (history or [])[-CHAT_MAX_MESSAGES:]:
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        role = msg.get("role")
        messages.append({
            "role": role if role in ("user", "assistant") else "user",
            "content": content[:CHAT_MAX_CHARS],
        })
    return messages


def render_rule_chat_answer(ctx: Dict[str, Any]) -> str:
    """未启用模型时的兜底答复：照实复述结构化结论，不假装是对话。"""
    if ctx.get("task") == "weather":
        if not ctx.get("available"):
            return (f"当前未启用对话模型，且{ctx.get('reason') or '气象数据暂不可用'}，"
                    "无法给出实况。")
        lines = ["当前未启用对话模型，以下是取到的实况："]
        for p in (ctx.get("points") or []):
            if not p.get("available"):
                lines.append(f"- {p.get('name')}：{p.get('reason') or '缺测'}")
                continue
            temp = f"，{p['temp_c']}℃" if p.get("temp_c") is not None else ""
            wind = f"，风速 {p['wind_speed_ms']} m/s" if p.get("wind_speed_ms") is not None else ""
            lines.append(
                f"- {p.get('name')}：TKE {p.get('tke')}，EDR {p.get('edr')}，"
                f"等级 {p.get('level')}{wind}{temp}"
            )
        lines.append(f"（取数时刻 {ctx.get('query_time') or '—'} {ctx.get('timezone') or ''}，"
                     f"高度层 {ctx.get('level_note') or '—'}）")
        return "\n".join(lines)

    if ctx.get("task") == "time_window":
        rec = ctx.get("recommended") or {}
        window = rec.get("window") or []
        span = f"（窗口 {window[0]}–{window[1]}）" if len(window) >= 2 else ""
        lines = [
            "当前未启用对话模型，我只能复述这次扫描的结果：",
            f"- 推荐起飞：{rec.get('takeoff') or '未给出'}{span}",
            f"- 该时段风险等级：{rec.get('level') or '—'}",
            f"- 安全时段：{ctx.get('safe_slot_count', 0)} 个",
        ]
        return "\n".join(lines)

    risk = ctx.get("risk") or {}
    distance = ctx.get("distance") or {}
    lines = [
        "当前未启用对话模型，我只能复述这次评估的结果：",
        f"- 结论：{ctx.get('conclusion') or '—'}",
        f"- 全程风险：{risk.get('overall') or '—'}，最大 TKE {risk.get('max_tke') if risk.get('max_tke') is not None else '—'}",
        f"- 航线里程：{distance.get('route_km') if distance.get('route_km') is not None else '—'} km，"
        f"绕行比 {distance.get('detour_ratio') if distance.get('detour_ratio') is not None else '—'}×",
    ]
    return "\n".join(lines)


def generate_chat_stream(ctx: Dict[str, Any], history: List[Dict[str, Any]]) -> Iterator[Dict[str, Any]]:
    """对话的统一入口，事件类型与 ``generate_report_stream`` 完全一致。

    来源声明同样要晚于「确认真拿到了内容」：先 ``meta`` 再失败会让前端的
    来源标签闪一下再跳变。已吐出正文后不能再降级，否则兜底文案会和残稿叠在一起。
    """
    if llm_available():
        announced = False
        try:
            for piece in stream_llm_messages(build_chat_messages(ctx, history)):
                if not announced:
                    yield {"type": "meta", "source": "llm"}
                    announced = True
                yield {"type": "delta", "text": piece}
            if announced:
                yield {"type": "done"}
                return
            yield {
                "type": "error",
                "message": "模型未返回任何内容，已改为复述评估结论。",
            }
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
            if announced:
                yield {
                    "type": "error",
                    "message": f"模型输出中断（{type(exc).__name__}），以上为已生成的部分内容。",
                }
                yield {"type": "done"}
                return
            yield {
                "type": "error",
                "message": f"模型调用失败（{type(exc).__name__}），已改为复述评估结论。",
            }

    yield {"type": "meta", "source": "rule"}
    yield {"type": "delta", "text": render_rule_chat_answer(ctx)}
    yield {"type": "done"}
