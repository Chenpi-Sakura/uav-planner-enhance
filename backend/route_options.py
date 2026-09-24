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

TIER_LABELS: Dict[str, str] = {
    "urgent": "紧急",
    "normal": "平衡",
    "economy": "安全",
}


def _signature(path) -> tuple:
    return tuple((round(float(p.lat), 2), round(float(p.lon), 2)) for p in path)


def plan_route_options(req) -> Dict[str, Any]:
    """按 settings.PLAN_TIERS 逐档规划。req 需为 schemas.PlanRequest。"""
    straight_km = _haversine_km(float(req.start.lat), float(req.start.lon),
                                float(req.end.lat), float(req.end.lon))
    routes: List[Dict[str, Any]] = []
    sigs: List[tuple] = []

    for tier in settings.PLAN_TIERS:
        scale = tier_risk_scale(tier)
        plan = plan_path_ex(req.model_copy(update={"priority": tier}))
        item: Dict[str, Any] = {
            "priority": tier,
            "label": TIER_LABELS.get(tier, tier),
            "risk_scale": scale,
            "feasible": bool(plan.found and plan.path),
            "reason": None if (plan.found and plan.path) else (plan.reason or "blocked"),
            "path": [],
            "total_distance": 0.0,
            "detour_ratio": None,
            "max_tke": 0.0,
            "max_edr": 0.0,
            "overall_level": "",
            "moderate_ratio": 0.0,
            "high_ratio": 0.0,
            "risk_engaged": False,
            "n_nodes": 0,
        }
        if item["feasible"]:
            samples = [
                {"lat": float(p.lat), "lon": float(p.lon),
                 "tke": t, "edr": e}
                for p, t, e in zip(plan.path, plan.tkes, plan.edrs)
            ]
            prof = build_risk_profile(samples)
            item.update({
                "path": [{"lat": float(p.lat), "lon": float(p.lon)} for p in plan.path],
                "total_distance": round(float(plan.total_distance), 1),
                "detour_ratio": (round(float(plan.total_distance) / straight_km, 4)
                                 if straight_km > 1e-6 else None),
                "max_tke": prof.stats.max_tke,
                "max_edr": prof.stats.max_edr,
                "overall_level": prof.overall,
                "moderate_ratio": prof.stats.moderate_ratio,
                "high_ratio": prof.stats.high_ratio,
                "risk_engaged": prof.stats.moderate_ratio > 0 or prof.stats.high_ratio > 0,
                "n_nodes": len(plan.path),
            })
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
