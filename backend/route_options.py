"""三方案航线：同一起终点一次产出「紧急 / 平衡 / 安全」三条线。

三档的差别只进 A* 的湍流软惩罚倍率（见 planner.tier_risk_scale），硬阈值
（TKE≥26 / EDR≥0.376 与 NaN 禁区）对三档完全一致——安全底线不由产品档位决定。

风险统计直接复用规划期记录的逐节点 tke/edr（即 A* 判定时看到的值），
不再沿程重采样，因此不产生额外 NetCDF I/O。

三档规划完还要仲裁一次「越保守绝不交出更贵的线」（enforce_tier_monotonicity）：
4D 的代价随到达时刻变，A* 单标签的支配判定不保证最优，实测确实出现过安全档
比平衡档更热更贵的一刻。仲裁只在三档已算出的线里挑，零额外取数。
"""
from typing import Any, Dict, List, Optional, Tuple

from config import settings
from planner import (plan_cost_km, plan_path_ex, search_deadline, search_effort_of,
                     tier_risk_scale, _haversine_km)
from risk import build_risk_profile
from schemas import PLANNER_REASON_VERDICT, VERDICT_LABELS, verdict_of

TIER_LABELS: Dict[str, str] = {
    "urgent": "紧急",
    "normal": "平衡",
    "economy": "安全",
}


def _usable(plan) -> bool:
    return bool(plan is not None and plan.found and plan.path)


def enforce_tier_monotonicity(plans: Dict[str, Any],
                              tiers: List[str]) -> List[Dict[str, Any]]:
    """每档最终交出的线，必须是候选里在【本档倍率】下最便宜的那条；就地改写 plans，返回换线记录。

    为什么需要：4D 的代价随到达时刻变化，而 A* 每个格点只留一个标签（g 与由它推出的
    时刻），被 g 支配剪掉的那条前缀踩的是另一帧天气。于是"更保守的档位交出一条在自己
    尺子下更贵的线"确实会发生——2026-09-26 实测 04-17 04:04 林芝→昌都：安全档交付的线
    代价 4123，而同一次点击里平衡档交出的线只要 1205。这里不修搜索，只在三档之间仲裁
    一次：谁的线在本档倍率下便宜就用谁的。

    换线不引入合法性风险：硬阈值三档共用，到达时刻只由几何/速度/起飞时刻决定，
    兄弟档那条线在本档同样是逐点判过的。代价是 search_effort 会跟着线一起过来
    （它记录的是"这条线是哪次搜索算出来的"），换线记录里写明来源。
    """
    candidates = dict(plans)
    swaps: List[Dict[str, Any]] = []
    for tier in tiers:
        mine = candidates.get(tier)
        if not _usable(mine):
            continue
        scale = tier_risk_scale(tier)
        best, best_cost, best_tier = mine, plan_cost_km(mine, scale), tier
        for other, cand in candidates.items():
            if other == tier or not _usable(cand):
                continue
            cost = plan_cost_km(cand, scale)
            if cost < best_cost - 1e-9:
                best, best_cost, best_tier = cand, cost, other
        if best_tier != tier:
            plans[tier] = best
            swaps.append({"tier": tier, "adopted_from": best_tier,
                          "risk_scale": scale,
                          "cost_own_km": round(plan_cost_km(mine, scale), 1),
                          "cost_adopted_km": round(best_cost, 1)})
    return swaps


def plan_tiers_with_guard(make_request, deadline: Optional[float] = None
                          ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """按 settings.PLAN_TIERS 逐档规划 + 一次档位单调性复检。

    make_request(tier) 需返回 schemas.PlanRequest；deadline 是整次请求共用的墙钟余额。
    返回（每档最终采用的 PlanResult, 换线记录）。两个多档入口共用
    （`/api/route/plan-multi` 与 `/api/assess/feasibility-multi`）；
    功能二只按平衡档规划，不经过这里。
    """
    if deadline is None:
        deadline = search_deadline(settings.ASTAR_REQUEST_TIME_LIMIT_S)
    tiers = list(settings.PLAN_TIERS)
    plans = {tier: plan_path_ex(make_request(tier), deadline=deadline) for tier in tiers}
    return plans, enforce_tier_monotonicity(plans, tiers)


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
        "search_effort": search_effort_of(plan),
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
    """按 settings.PLAN_TIERS 逐档规划，再仲裁一次单调性。req 需为 schemas.PlanRequest。

    三档共用一份墙钟余额（一次点击 = 一次请求），余额耗尽的档仍留在结果里标「未算出」。
    """
    straight_km = _haversine_km(float(req.start.lat), float(req.start.lon),
                                float(req.end.lat), float(req.end.lon))
    plans, swaps = plan_tiers_with_guard(
        lambda tier: req.model_copy(update={"priority": tier}))
    routes: List[Dict[str, Any]] = []
    sigs: List[tuple] = []

    for tier in settings.PLAN_TIERS:
        plan = plans[tier]
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
        # 某档采用了兄弟档的线（本档倍率下更便宜），见 enforce_tier_monotonicity
        "tier_swaps": swaps,
    }
    return {"routes": routes, "meta": meta}
