"""三方案航线：同一起终点一次产出「紧急 / 平衡 / 安全」三条线。

三档的差别只进 A* 的湍流软惩罚倍率（见 planner.tier_risk_scale），硬阈值
（TKE≥26 / EDR≥0.376 与 NaN 禁区）对三档完全一致——安全底线不由产品档位决定。

风险统计直接复用规划期记录的逐节点 tke/edr（即 A* 判定时看到的值），
不再沿程重采样，因此不产生额外 NetCDF I/O。
"""
from typing import Any, Dict, List

from config import settings
from planner import plan_path_ex, tier_risk_scale, _haversine_km
from risk import build_risk_profile
from schemas import PLANNER_REASON_VERDICT, VERDICT_LABELS, verdict_of

TIER_LABELS: Dict[str, str] = {
    "urgent": "紧急",
    "normal": "平衡",
    "economy": "安全",
}


def _signature(path) -> tuple:
    return tuple((round(float(p.lat), 2), round(float(p.lon), 2)) for p in path)


def summarize_plan(plan, straight_km: float, speed: float = 0.0) -> Dict[str, Any]:
    """把一次规划压成公共形状：几何 + 里程 + 绕行比 + 沿程风险统计。

    功能一（三档）与功能二（Top-K）都要这套汇总，各写一遍迟早会漂移。
    风险统计直接取规划期逐节点的 tke/edr（A* 判定时看到的值），不重采样。
    """
    feasible = bool(plan.found and plan.path)
    reason = None if feasible else (plan.reason or "blocked")
    verdict = verdict_of(feasible, reason, PLANNER_REASON_VERDICT)
    item: Dict[str, Any] = {
        "feasible": feasible,
        # 「没搜完」不是「没有航线」：功能二的候选行要用它替换风险档那一格
        "verdict": verdict,
        "conclusion": VERDICT_LABELS[verdict],
        "reason": reason,
        "path": [],
        "total_distance": 0.0,
        "detour_ratio": None,
        "flight_time_h": None,
        "max_tke": 0.0,
        "max_edr": 0.0,
        "overall_level": "",
        "moderate_ratio": 0.0,
        "high_ratio": 0.0,
        "risk_engaged": False,
        "n_nodes": 0,
    }
    if not feasible:
        return item

    samples = [{"lat": float(p.lat), "lon": float(p.lon), "tke": t, "edr": e}
               for p, t, e in zip(plan.path, plan.tkes, plan.edrs)]
    prof = build_risk_profile(samples)
    km = float(plan.total_distance)
    item.update({
        "path": [{"lat": float(p.lat), "lon": float(p.lon)} for p in plan.path],
        "total_distance": round(km, 1),
        "detour_ratio": (round(km / straight_km, 4) if straight_km > 1e-6 else None),
        "flight_time_h": (round(km / float(speed), 2) if speed else None),
        "max_tke": prof.stats.max_tke,
        "max_edr": prof.stats.max_edr,
        "overall_level": prof.overall,
        "moderate_ratio": prof.stats.moderate_ratio,
        "high_ratio": prof.stats.high_ratio,
        "risk_engaged": prof.stats.moderate_ratio > 0 or prof.stats.high_ratio > 0,
        "n_nodes": len(plan.path),
    })
    return item


def plan_route_options(req) -> Dict[str, Any]:
    """按 settings.PLAN_TIERS 逐档规划。req 需为 schemas.PlanRequest。"""
    straight_km = _haversine_km(float(req.start.lat), float(req.start.lon),
                                float(req.end.lat), float(req.end.lon))
    routes: List[Dict[str, Any]] = []
    sigs: List[tuple] = []

    for tier in settings.PLAN_TIERS:
        plan = plan_path_ex(req.model_copy(update={"priority": tier}))
        item: Dict[str, Any] = {
            "priority": tier,
            "label": TIER_LABELS.get(tier, tier),
            "risk_scale": tier_risk_scale(tier),
            **summarize_plan(plan, straight_km),
        }
        if item["feasible"]:
            sigs.append(_signature(plan.path))
        routes.append(item)

    unique = len(set(sigs))
    meta = {
        "straight_km": round(straight_km, 1),
        "tiers": list(settings.PLAN_TIERS),
        "n_feasible": sum(1 for r in routes if r["feasible"]),
        # 三条线几何完全相同：多半是该时段沿程湍流未超过中风险下沿，惩罚未生效
        "all_identical": bool(sigs) and unique == 1,
        "distinct_paths": unique,
    }
    return {"routes": routes, "meta": meta}
