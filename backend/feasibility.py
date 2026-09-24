"""
功能一 · 飞行路径可行性评估编排。

输出明确的「可飞 / 不可飞」结论：
  - 可飞：给出航线总距离、直线距离、预计飞行时间、全程与分段风险等级；
  - 不可飞：给出阻塞原因、风险分布、绕行成本分析与建议措施。

成本口径：按会议决议，仅考虑【时间成本】（绕行时间增量与优先级阈值比较）。
"""
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Any, Optional, Tuple
import math

import numpy as np

from config import settings
from planner import plan_path_ex, PlanResult, _haversine_km
from data_loader import data_loader
from timebase import to_data_time_str
from risk import build_risk_profile, RiskProfile, LEVEL_UNFLYABLE
import blockage as blockage_mod
import geo

# 结论常量
FEASIBLE = "可飞"
INFEASIBLE = "不可飞"

# 替代方案排序用：风险越低越优先
RISK_ORDER = {"低风险": 0, "中风险": 1, "高风险": 2, "不可飞": 3}

# 不可飞原因
REASON_NO_PATH = "no_safe_path"                 # 无安全路径（被高危湍流封死）
REASON_DETOUR = "detour_exceeds_threshold"      # 绕行成本超阈值
REASON_DATA_MISSING = "data_coverage_insufficient"
REASON_ITERATION = "search_iteration_limit"


@dataclass
class FeasibilityResult:
    feasible: bool
    conclusion: str
    reason: Optional[str]
    priority: str
    detour_limit: float
    distance: Dict[str, Any]
    flight_time_h: float
    risk: Optional[Dict[str, Any]]
    blocking: Optional[Dict[str, Any]]
    suggestions: Dict[str, Any]
    path: List[Dict[str, float]]
    meta: Dict[str, Any] = field(default_factory=dict)


def assess_feasibility(req,
                       samples_per_km: float = 0.1,
                       include_alt_bases: bool = True,
                       include_alt_windows: bool = True) -> FeasibilityResult:
    """执行功能一评估。

    req 需为 schemas.FeasibilityRequest。
    """
    priority = (req.priority or settings.DEFAULT_PRIORITY).lower()
    detour_limit = settings.DETOUR_RATIO_LIMIT.get(priority, settings.DETOUR_RATIO_LIMIT["normal"])

    start = (float(req.start.lat), float(req.start.lon))
    end = (float(req.end.lat), float(req.end.lon))

    # 1) 直线基线
    straight_km = _haversine_km(start[0], start[1], end[0], end[1])
    if straight_km > 1e-6:
        straight_time_h = straight_km / req.speed
    else:
        straight_time_h = 0.0

    # 2) 规划（含途经点）
    plan_req = _to_plan_request(req, priority)
    plan: PlanResult = plan_path_ex(plan_req)

    path_dicts: List[Dict[str, float]] = [
        {"lat": p.lat, "lon": p.lon} for p in plan.path
    ]

    # 3) 数据覆盖检查 + 沿程采样
    data_ok = _check_coverage(start) and _check_coverage(end)

    risk_profile: Optional[RiskProfile] = None
    if plan.found and plan.path:
        samples = _sample_along_path(
            plan.path, req.date, req.start_time, req.speed,
            times=list(getattr(plan, 'times', []) or []),
            tkes=list(getattr(plan, 'tkes', []) or []),
            edrs=list(getattr(plan, 'edrs', []) or []),
        )
        seg_bounds = geo.build_segment_bounds(samples)
        risk_profile = build_risk_profile(
            samples, seg_bounds,
            tke_high=req.tke_threshold, edr_high=req.edr_threshold,
        )

    route_km = plan.total_distance if plan.found else 0.0
    flight_time_h = (route_km / req.speed) if req.speed else 0.0
    detour_ratio = (route_km / straight_km) if straight_km > 1e-6 else 1.0
    extra_time_h = max(0.0, flight_time_h - straight_time_h)

    # 未规划出航线时，航线距离/飞行时间/绕行比/绕行成本全部没有意义。
    # 这里显式标记 applicable=False，前端据此显示「不适用」，
    # 避免把 0 km / 0× / +0h 之类的占位数字当成真实评估结果展示。
    has_route = bool(plan.found and plan.path)

    distance_block = {
        "applicable": has_route,
        "straight_km": round(straight_km, 2),
        "route_km": round(route_km, 2) if has_route else None,
        "detour_ratio": round(detour_ratio, 3) if has_route else None,
        "flight_time_h": round(flight_time_h, 2) if has_route else None,
        "time_cost_h": {
            "applicable": has_route,
            "extra": round(extra_time_h, 2) if has_route else None,
            "limit": round(straight_time_h * (detour_limit - 1.0), 2),
            "limit_ratio": detour_limit,
            "exceeded": bool(has_route and detour_ratio > detour_limit + 1e-9),
        },
    }

    blocking_block = None
    suggestions: Dict[str, Any] = {"alt_windows": [], "alt_bases": [], "text": ""}

    # 起终点自身的硬约束校验。规划器在起点不安全时会直接判定 blocked，
    # 若只按「航路被封死」解释，会把原因错误地归到中途湍流区上。
    endpoints = _check_endpoints(start, end, req)
    endpoint_bad = _bad_endpoint_label(endpoints)

    # 4) 结论判定
    if not data_ok:
        feasible = False
        reason = REASON_DATA_MISSING
    elif not plan.found:
        feasible = False
        if plan.reason == 'no_data':
            # 日期或区域没有气象数据，与「湍流封死」是两回事，别去分析阻塞区
            reason = REASON_DATA_MISSING
        else:
            reason = REASON_ITERATION if plan.reason == 'iteration_limit' else REASON_NO_PATH
            # 起降点本身不安全时，不必再报告途中阻塞区，避免张冠李戴
            if endpoint_bad is None:
                blocking_block = _analyze_blocking(start, end, req)
    elif risk_profile is not None and risk_profile.overall == LEVEL_UNFLYABLE:
        feasible = False
        reason = REASON_NO_PATH
        blocking_block = _analyze_blocking(start, end, req)
    elif distance_block["time_cost_h"]["exceeded"]:
        feasible = False
        reason = REASON_DETOUR
    else:
        feasible = True
        reason = None

    # 5) 建议措施
    # 先算替代起降点，再生成文案：文案需要知道是否真的给出了替代点，
    # 否则会出现「文案承诺已给出替代点、但列表为空」的自相矛盾。
    # 两个开关各自独立、且都只在不可飞时才算——这两项各要再跑若干次规划/取数。
    if not feasible:
        if include_alt_windows and data_ok:
            suggestions["alt_windows"] = _suggest_alt_windows(start, end, req)
        if include_alt_bases:
            suggestions["alt_bases"] = _suggest_alt_bases(req, endpoints=endpoints)
    suggestions["has_alt_bases"] = bool(suggestions["alt_bases"])
    suggestions["text"] = _build_suggestion_text(
        feasible, reason, distance_block, blocking_block, risk_profile, priority,
        endpoint_bad=endpoint_bad,
        has_alt_bases=suggestions["has_alt_bases"],
    )

    return FeasibilityResult(
        feasible=feasible,
        conclusion=FEASIBLE if feasible else INFEASIBLE,
        reason=reason,
        priority=priority,
        detour_limit=detour_limit,
        distance=distance_block,
        flight_time_h=round(flight_time_h, 2),
        risk=risk_profile.to_dict() if risk_profile else None,
        blocking=blocking_block,
        suggestions=suggestions,
        path=path_dicts,
        meta={
            "date": req.date,
            "start_time": req.start_time,
            "speed_kmh": req.speed,
            "waypoints_reached": plan.waypoints_reached,
            "plan_reason": plan.reason,
            "data_coverage_ok": data_ok,
            "endpoints": endpoints,
            "unsafe_endpoint": endpoint_bad,
            "grid_resolution_deg": settings.GRID_RESOLUTION_DEG,
        },
    )


def to_response_dict(res: FeasibilityResult) -> Dict[str, Any]:
    """FeasibilityResult -> FeasibilityResponse 的字段字典（单档与三档共用）。"""
    return {
        "feasible": res.feasible,
        "conclusion": res.conclusion,
        "reason": res.reason,
        "priority": res.priority,
        "detour_limit": res.detour_limit,
        "distance": res.distance,
        "flight_time_h": res.flight_time_h,
        "risk": res.risk,
        "blocking": res.blocking,
        "suggestions": res.suggestions,
        "path": res.path,
        "meta": res.meta,
    }


def assess_feasibility_multi(req) -> Dict[str, Any]:
    """一次点击同时产出「紧急 / 平衡 / 安全」三档的完整评估。

    只有当前高亮档（默认平衡）算替代起降点与替代时段：那两项服务的是详情面板，
    三档各算一遍会把取数放大三倍而卡片上用不到。阻塞区只在不可飞时才分析，
    且与档位无关，重复开销可控。
    """
    tiers = list(settings.PLAN_TIERS)
    active = (getattr(req, "priority", None) or settings.DEFAULT_PRIORITY).lower()
    if active not in tiers:
        active = settings.DEFAULT_PRIORITY

    results: List[Dict[str, Any]] = []
    sigs: List[tuple] = []
    for tier in tiers:
        is_active = tier == active
        res = assess_feasibility(
            req.model_copy(update={"priority": tier}),
            include_alt_bases=is_active,
            include_alt_windows=is_active,
        )
        payload = to_response_dict(res)
        results.append(payload)
        if res.path:
            sigs.append(tuple((round(float(p["lat"]), 4), round(float(p["lon"]), 4))
                              for p in res.path))

    meta = {
        "tiers": tiers,
        "active": active,
        "n_feasible": sum(1 for r in results if r["feasible"]),
        # 三档几何完全相同：多半是该时段沿程湍流未超过中风险下沿，软惩罚未参与代价
        "all_identical": bool(sigs) and len(set(sigs)) == 1,
        "distinct_paths": len(set(sigs)),
    }
    return {"results": results, "active": active, "meta": meta}


def _to_plan_request(req, priority: str):
    from schemas import PlanRequest
    return PlanRequest(
        start=req.start,
        end=req.end,
        waypoints=req.waypoints or [],
        date=req.date,
        start_time=req.start_time,
        speed=req.speed,
        priority=priority,
        tke_threshold=req.tke_threshold,
        edr_threshold=req.edr_threshold,
    )


def _check_coverage(pt: Tuple[float, float]) -> bool:
    """检查给定坐标是否落在气象数据覆盖范围内。"""
    try:
        ds = data_loader.ds_u
        if ds is None:
            return True  # 数据未加载时不阻塞评估流程
        lats = ds.lat.values
        lons = ds.lon.values
        lat_lo, lat_hi = float(np.min(lats)), float(np.max(lats))
        lon_lo, lon_hi = float(np.min(lons)), float(np.max(lons))
        return (lat_lo - 0.25 <= pt[0] <= lat_hi + 0.25) and (lon_lo - 0.25 <= pt[1] <= lon_hi + 0.25)
    except Exception:
        return True


def _sample_along_path(path, date: str, start_time: str, speed: float,
                       times: Optional[List[Any]] = None,
                       tkes: Optional[List[float]] = None,
                       edrs: Optional[List[float]] = None) -> List[Dict[str, Any]]:
    """沿航线构造风险样本。

    优先直接复用 planner 在规划过程中已判定的 TKE/EDR（tkes/edrs），
    这样「规划可行」与「风险评估」使用完全相同的数值，不会出现
    「A* 判可行、风险评估判不可飞」的矛盾结论。
    仅当 planner 未提供数值（如候选点评估）时才回退到独立查表。
    """
    pts = [{"lat": float(p.lat), "lon": float(p.lon)} for p in path]
    if len(pts) < 2:
        pts = pts * 2 if pts else []

    resampled: List[Dict[str, float]] = []
    cum = 0.0
    for i, p in enumerate(pts):
        if i > 0:
            cum += _haversine_km(pts[i-1]["lat"], pts[i-1]["lon"], p["lat"], p["lon"])
        resampled.append({"lat": p["lat"], "lon": p["lon"], "cum_km": cum})

    if not resampled:
        return []

    reuse = (tkes is not None and edrs is not None
             and len(tkes) == len(resampled) and len(edrs) == len(resampled))

    if reuse:
        tke_list = [float(v) for v in tkes]
        edr_list = [float(v) for v in edrs]
    else:
        lats = np.array([p["lat"] for p in resampled])
        lons = np.array([p["lon"] for p in resampled])
        if times and len(times) == len(resampled):
            times_arr = np.array([np.datetime64(t) for t in times], dtype='datetime64[ns]')
        else:
            # 北京时间 -> 数据标签时间（UTC），否则取到偏早 8 小时的气象场
            start_ts = np.datetime64(to_data_time_str(f"{date}T{start_time}"))
            times_arr = np.array([
                start_ts + np.timedelta64(int((p["cum_km"] / speed) * 3600), 's')
                for p in resampled
            ], dtype='datetime64[ns]')
        q_tke, q_edr = _batch_turbulence(lats, lons, times_arr)
        tke_list = [float(v) for v in q_tke] if q_tke is not None else [0.0]*len(resampled)
        edr_list = [float(v) for v in q_edr] if q_edr is not None else [0.0]*len(resampled)

    samples = []
    for i, p in enumerate(resampled):
        samples.append({
            "lat": p["lat"],
            "lon": p["lon"],
            "cum_km": round(p["cum_km"], 3),
            "tke": tke_list[i],
            "edr": edr_list[i],
        })
    return samples


def _batch_turbulence(lats: np.ndarray, lons: np.ndarray, times: np.ndarray):
    """批量查询沿程 TKE/EDR（矢量化的最近邻索引查表）。"""
    try:
        if not (data_loader._loaded and data_loader.ds_u is not None):
            return None, None

        # 取包围盒的一天数据，转成内存网格后按最近邻索引查询
        pad = 1.0
        tke_grid, edr_grid, g_lats, g_lons, g_times = data_loader.get_meteorology_grid(
            float(lats.min()) - pad, float(lats.max()) + pad,
            float(lons.min()) - pad, float(lons.max()) + pad,
            date=str(times[0]),
        )
        if tke_grid is None:
            return None, None

        idx_t = np.abs(g_times[:, None] - times[None, :]).argmin(axis=0)
        idx_la = np.abs(g_lats[:, None] - lats[None, :]).argmin(axis=0)
        idx_lo = np.abs(g_lons[:, None] - lons[None, :]).argmin(axis=0)

        return tke_grid[idx_t, idx_la, idx_lo], edr_grid[idx_t, idx_la, idx_lo]
    except Exception:
        return None, None


def _analyze_blocking(start, end, req) -> Optional[Dict[str, Any]]:
    """分析导致不可飞的阻塞性危险区。

    重要：必须按【实际飞行窗口】取危险区并集，而不是整日平均。
    否则会把只在起飞窗口内出现、真正封死航路的那块危险区稀释掉，
    报出的阻塞区会既不在走廊上、也与实际失败原因不符。
    """
    try:
        pad = 1.0
        tke_grid, edr_grid, lats, lons, times = data_loader.get_meteorology_grid(
            min(start[0], end[0]) - pad, max(start[0], end[0]) + pad,
            min(start[1], end[1]) - pad, max(start[1], end[1]) + pad,
            date=to_data_time_str(f"{req.date}T{req.start_time}"),
        )
        if tke_grid is None:
            return None

        # 飞行窗口：直线距离按任务速度折算，并留出 1 小时余量
        straight_km = _haversine_km(start[0], start[1], end[0], end[1])
        speed = float(req.speed) if getattr(req, "speed", None) else 60.0
        window_hours = max(1.0, straight_km / speed * 1.5 + 1.0)

        regions = blockage_mod.analyze_blockage(
            tke_grid, edr_grid, lats, lons, start, end,
            tke_thresh=req.tke_threshold, edr_thresh=req.edr_threshold,
            times=times,
            base_time=to_data_time_str(f"{req.date}T{req.start_time}"),
            window_hours=window_hours,
        )
        return {
            "regions": blockage_mod.to_dict(regions),
            "dominant_shape": blockage_mod.dominant_shape(regions),
        }
    except Exception:
        return None


def _check_endpoints(start, end, req) -> Dict[str, Any]:
    """校验起降点自身是否满足硬约束。

    返回 {'start': {...}, 'end': {...}}，每项含 safe / tke / edr / describe。
    规划器对「起点不安全」是直接判 blocked 的，若不单独说明，
    用户会误以为是航路中段的湍流区封死了航线。
    """
    out: Dict[str, Any] = {}
    for key, pt in (("start", start), ("end", end)):
        tke = float("nan")
        edr = float("nan")
        safe = True
        try:
            vals = data_loader.get_meteorology_at_points(
                [pt[0]], [pt[1]], date=to_data_time_str(f"{req.date}T{req.start_time}"))
            tke = float(vals["tke"][0])
            edr = float(vals["edr"][0])
            tke_th = req.tke_threshold if req.tke_threshold is not None else settings.TKE_THRESHOLD_HIGH
            edr_th = req.edr_threshold if req.edr_threshold is not None else settings.EDR_THRESHOLD_HIGH
            safe = not (tke >= tke_th or edr >= edr_th)
        except Exception:
            pass
        out[key] = {
            "safe": bool(safe),
            "tke": round(tke, 4) if tke == tke else None,
            "edr": round(edr, 4) if edr == edr else None,
            "describe": geo.describe_point(pt[0], pt[1]),
        }
    return out


def _bad_endpoint_label(endpoints: Optional[Dict[str, Any]]) -> Optional[str]:
    """返回不安全的起降点标签：'起点' / '终点' / '起终点'；都安全则 None。"""
    if not endpoints:
        return None
    bad = []
    if not endpoints.get("start", {}).get("safe", True):
        bad.append("起点")
    if not endpoints.get("end", {}).get("safe", True):
        bad.append("终点")
    if not bad:
        return None
    return "".join(bad) if len(bad) == 2 else bad[0]


def _suggest_alt_windows(start, end, req, max_results: int = 3) -> List[Dict[str, Any]]:
    """扫描当天的安全时段，作为「更换飞行时间」建议的数据支撑。

    复用功能二的扫描链路（同一时区口径与阈值），只回填少量字段给前端展示，
    不重复实现一套时段判定逻辑，避免两处口径漂移。
    """
    try:
        from schemas import TimeWindowRequest
        from time_window import assess_time_window

        tw_req = TimeWindowRequest(
            start=req.start, end=req.end, waypoints=req.waypoints or [],
            date=req.date, start_time=req.start_time or "08:00", speed=req.speed,
            forecast_days=0,  # 只要当天的备选时段
            tke_threshold=getattr(req, "tke_threshold", None),
            edr_threshold=getattr(req, "edr_threshold", None),
        )
        tw = assess_time_window(tw_req, run_planner=False)
    except Exception:
        return []

    out: List[Dict[str, Any]] = []
    rec = tw.get("recommended")
    if rec:
        out.append({
            "date": tw.get("date"),
            "takeoff": rec.get("takeoff"),
            "window": rec.get("window"),
            "level": rec.get("level"),
            "max_tke": rec.get("max_tke"),
        })
    for w in (tw.get("backup_windows") or []):
        if len(out) >= max_results:
            break
        out.append({
            "date": tw.get("date"),
            "takeoff": w.get("start"),
            "window": [w.get("start"), w.get("end")],
            "level": w.get("level"),
            "max_tke": w.get("max_tke"),
        })
    return out[:max_results]


def _suggest_alt_bases(req,
                       endpoints: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """以起降点为圆心按方位/半径采样，返回可飞的替代起降点。

    起点不安全时挪起点、终点不安全时挪终点：只挪不安全的那一端，
    另一端保持不动，这样给出的方案才是「改一个点就能飞」。
    两端都安全（即真正卡在中途湍流区）时，优先尝试更换终点。
    """
    from schemas import PlanRequest
    from planner import plan_path_ex as _plan

    endpoints = endpoints or {}
    bad = _bad_endpoint_label(endpoints)
    # 需要挪动哪一端：bad 为 None 时默认挪终点
    move_start = bool(bad and "起点" in bad)
    move_end = bool(bad is None or "终点" in bad)
    # 两端都不安全时先挪起点，跑完再补终点方案
    targets = []
    if move_start:
        targets.append("start")
    if move_end:
        targets.append("end")
    if not targets:
        targets = ["end"]

    results: List[Dict[str, Any]] = []
    # 方位顺序按「远离原航向」优先排列，避免替代点全挤在同一侧
    bearings = [(0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)]

    for target in targets:
        anchor = req.start if target == "start" else req.end
        other = req.end if target == "start" else req.start

        # 先收集全部候选，再按「风险等级 -> 绕行距离」排序取前 N，
        # 这样给出的替代点分散在不同方位，而不是同一方向的近/中/远三点。
        candidates: List[Dict[str, Any]] = []
        seen_dirs: set = set()

        for radius in settings.ALT_BASE_RADII_KM:
            for b_lat, b_lon in bearings:
                if (b_lat, b_lon) in seen_dirs:
                    continue
                cand_lat = float(anchor.lat) + b_lat * (radius / 111.0)
                cand_lon = float(anchor.lon) + b_lon * (radius / (111.0 * math.cos(math.radians(float(anchor.lat)))))

                if target == "start":
                    s_pt = type(req.start)(lat=cand_lat, lon=cand_lon)
                    e_pt = other
                else:
                    s_pt = other
                    e_pt = type(req.end)(lat=cand_lat, lon=cand_lon)

                try:
                    cand_req = PlanRequest(
                        start=s_pt,
                        end=e_pt,
                        waypoints=[],
                        date=req.date,
                        start_time=req.start_time,
                        speed=req.speed,
                        tke_threshold=req.tke_threshold,
                        edr_threshold=req.edr_threshold,
                    )
                    r = _plan(cand_req)
                    if r.found and r.path:
                        samples = _sample_along_path(
                            r.path, req.date, req.start_time, req.speed,
                            times=list(getattr(r, 'times', []) or []),
                            tkes=list(getattr(r, 'tkes', []) or []),
                            edrs=list(getattr(r, 'edrs', []) or []),
                        )
                        prof = build_risk_profile(samples)
                        if prof.overall != LEVEL_UNFLYABLE:
                            candidates.append({
                                "role": target,
                                "role_label": "替代起点" if target == "start" else "替代终点",
                                "end": {"lat": round(cand_lat, 4), "lon": round(cand_lon, 4)},
                                "level": prof.overall,
                                "distance_km": round(r.total_distance, 1),
                                "description": geo.describe_point(cand_lat, cand_lon),
                                "_dir": (b_lat, b_lon),
                            })
                except Exception:
                    continue

        # 同一方位只保留绕行最短的一个，再按风险等级、绕行距离排序
        candidates.sort(key=lambda c: (c["_dir"], c["distance_km"]))
        by_dir: Dict[Any, Dict[str, Any]] = {}
        for c in candidates:
            key = (c["role"], c["_dir"])
            if key not in by_dir:
                by_dir[key] = c
        ranked = sorted(
            by_dir.values(),
            key=lambda c: (RISK_ORDER.get(c["level"], 99), c["distance_km"]),
        )
        for c in ranked:
            if len(results) >= settings.ALT_BASE_MAX_RESULTS:
                break
            c.pop("_dir", None)
            results.append(c)

    return results


def _build_suggestion_text(feasible: bool, reason: Optional[str],
                           distance: Dict[str, Any],
                           blocking: Optional[Dict[str, Any]],
                           risk: Optional[RiskProfile],
                           priority: str,
                           endpoint_bad: Optional[str] = None,
                           has_alt_bases: bool = False) -> str:
    """生成建议措施文本（规则模板）。

    has_alt_bases 必须传入实际是否算出了替代起降点，避免文案承诺
    「系统已给出替代点」但列表为空的自相矛盾。
    """
    limit = distance["time_cost_h"]["limit_ratio"]

    if feasible:
        level = risk.overall if risk else "低风险"
        return (
            f"当前条件下存在安全可行航线，全程风险等级为{level}。"
            f"航线总距离 {distance['route_km']} km（直线 {distance['straight_km']} km，"
            f"绕行比 {distance['detour_ratio']}），预计飞行 {distance['flight_time_h']} 小时。"
            f"绕行成本在{_priority_label(priority)}任务的接受阈值（{limit}）之内，可执行任务。"
        )

    if reason == REASON_DATA_MISSING:
        return "起降点超出当前气象数据覆盖范围，无法评估，请调整任务区域或更新数据源。"

    if reason == REASON_ITERATION:
        return "搜索达到迭代上限，未能确认安全航线。建议放宽时间窗口或降低评估粒度后重试。"

    if reason == REASON_DETOUR:
        return (
            f"虽能避开高危湍流区，但绕行成本超出阈值：航线 {distance['route_km']} km，"
            f"绕行比 {distance['detour_ratio']}，超过{_priority_label(priority)}任务允许的 {limit}，"
            f"额外耗时 {distance['time_cost_h']['extra']} 小时。"
            "建议考虑：① 更换飞行时间（见时间窗口规划）；② 调整起降点；③ 放宽优先级或改用其他机型。"
        )

    # REASON_NO_PATH
    # 情况 A：起降点自身处在高危区，而不是航路中段被封死
    if endpoint_bad:
        head = f"无法执行任务：{endpoint_bad}本身处于高强度湍流区，起飞/降落阶段即超出安全阈值。"
        if has_alt_bases:
            tail = (
                f"建议措施：① 更换{endpoint_bad}（系统已给出可用替代点）；"
                "② 更换飞行时间；③ 推迟任务并关注气象更新。"
            )
        else:
            tail = (
                f"建议措施：① 更换{endpoint_bad}（当前搜索半径内未找到可用替代点，建议手动调整）；"
                "② 更换飞行时间；③ 推迟任务并关注气象更新。"
            )
        return head + tail

    shape_txt = ""
    if blocking and blocking.get("dominant_shape"):
        shape_txt = f"其中主要阻塞区呈{blocking['dominant_shape']}分布。"

    if has_alt_bases:
        tail = "建议措施：① 更换起降点（系统已给出可用替代点）；② 更换飞行时间；③ 推迟任务并关注气象更新。"
    else:
        tail = "建议措施：① 更换起降点（当前搜索半径内未找到可用替代点，建议手动调整）；② 更换飞行时间；③ 推迟任务并关注气象更新。"

    return "无法规划出安全航线：航路被高强度湍流区封死。" + shape_txt + tail


def _priority_label(priority: str) -> str:
    """档位中文名。键名沿用 urgent/normal/economy，只有界面与文案用这套字。"""
    return {"urgent": "紧急", "normal": "平衡", "economy": "安全"}.get(priority, "平衡")
