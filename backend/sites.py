r"""M7 站点自动规划（多航点模式）：给定机型与载重，在站表里选出最少的中继落地序列。

与 `_suggest_alt_bases` 的分工（backlog B11 三条边界之一）：那个回答「这一起降点不行，
换哪儿落」，候选是锚点周围 3 半径 × 8 方位的**自由格点**；本模块回答「这条走廊要在哪儿
落地才飞得完」，候选是 `sites.json` 里**离散的既有地名**。因为候选离散，主判据只能是
机型能力包与场地海拔——气象不再决定「哪个点入选」，只在同一跳位的并列候选之间排序与标注
（2026-09-27 裁定 ⑤）。

不改 `planner.py`：选出的中继就是 `PlanRequest.waypoints`，逐段 A\* 是现成的。本模块
最后做一次逐段复验，把「直线可达」换成「真实航线可达」，仅此而已。

两条不外推的纪律，都写在 reason 码上：
  - 载重超商载上限 → `payload_exceeds_max`（确定的物理否）
  - 载重在商载内但超出公开锚点 → `payload_beyond_published_envelope`（**没依据**，判未算出）

场地海拔不参与判定（2026-09-28 裁定）：站表的 elevation_m / elev_kind 只作展示与复核台账，
既不再筛站，也不判起降点——机型没有可引用的最大起降海拔依据。
"""
from __future__ import annotations

import heapq
import json
import math
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from config import ROOT_DIR, settings
from data_loader import data_loader
import assessor
import geo
from planner import SEGMENT_PAD_DEG, plan_path_ex, search_deadline, search_effort_of, _haversine_km
from risk import LEVEL_ORDER, build_risk_profile
from schemas import (PLANNER_REASON_VERDICT, SITE_REASON_VERDICT, VERDICT_FEASIBLE,
                     VERDICT_LABELS, VERDICT_UNDETERMINED, PlanRequest, Point,
                     SitePlanRequest, verdict_of)
from timebase import to_data_time_str, to_user_time_str

# ==================== 机队能力包（每台一份，逐字段带出处） ====================
#
# 为什么写在这里而不是 config.py：这些数字改一个字就等于改结论依据，属于「证据」不是「参数」，
# 和调参混在同一个文件里会让它们看起来可以随手拧。
# 完整出处清单见 docs/决策平台M7-站点自动规划方案.md 附录 A.1。
AIRCRAFT_PACKS: Dict[str, Dict[str, Any]] = {
    "td550": {
        # 界面不印型号：两台的区别只允许体现在续航、载重、段长与里程上。
        # 型号名留在 range_anchors / cert 这些内部字段与文档台账里。
        "label": "油动 · 商载上限 260 kg",
        "power": "油",
        "certified": True,
        "cert": "TD550D 2025-03 取得 CAAC 型号合格证（全球首张无人直升机系统 TC）",
        "max_payload_kg": 260.0,
        # 载荷 → 单程可飞距离（km，未扣余度）。由厂家公开的「载荷-航时」锚点乘巡航区间
        # **下限** 100 km/h 换算——取下限是为了不把速度算成利好：
        #   100 kg → 6.5 h ≈ 650 km   |   200 kg → 2 h = 200 km   |   260 kg → 2 h = 200 km
        # 「260 kg→2 h（产品页）」与「200 kg→2 h（中国交通报）」两个口径冲突，
        # 但换算后同为 200 km，冲突不影响本表。
        "range_anchors": [(100.0, 650.0), (200.0, 200.0), (260.0, 200.0)],
        "operational_cap_km": 200.0,   # 厂家「任务半径 200 km」，按单程口径读（2026-09-27 裁定 ②）
        # 巡航速度把里程换成时刻：4D 时间对齐、每跳到达时刻、逐段复验的起飞时间都吃它。
        # 取 100 是为了和上面的 range_anchors 同一把尺，别让段长按 100 说飞得完、
        # 时刻表按别的速度说飞多久（2026-09-28 补：此前两处共用界面那一个速度框）。
        "cruise_kmh": 100.0,
        "cruise_note": "厂家产品页巡航 100–120 km/h，取区间下限",
        "notes": [],
    },
    "v2000cg": {
        "label": "纯电 · 商载上限 400 kg",
        "power": "电",
        "certified": True,
        "cert": "TC 2024-03-22、PC、AC 2025-07-21（三证齐全）",
        "max_payload_kg": 400.0,
        # 只有 200 kg 一档有公开航程：厂家「商业运营航程 200 kg → 200 km」
        # （实飞旁证：约 200 kg / 161 km / 1 h）。400 kg 是**商载上限**不是航程锚点，
        # 该档无任何公开航程/航时数据 → 200 kg 以上一律判「超出公开依据」，不外推。
        "range_anchors": [(200.0, 200.0)],
        "operational_cap_km": None,
        # 厂家没有公布巡航速度；媒体那组「巡航可达 200 km/h」在附录 A.1 里登记、不采信。
        # 161 是**换算值**：实飞 200 kg / 161 km / 1 h 除出来的平均速度，界面上要标是换算的。
        "cruise_kmh": 161.0,
        "cruise_note": "换算值：实飞 200 kg／161 km／1 h 除得",
        "notes": [],
    },
}

# 海拔不再参与判定（2026-09-28 裁定）：站表里的 elevation_m / elev_kind / verify_priority
# 只作展示与复核台账用，既筛站也不判起降点，所以这里不再有任何门限常量与来源分级表。

CREW_BANNER = ("本系统机队内无高原载人合规机型（截至 2026-09，没有任何取证载人 eVTOL 宣称能在 "
               "3000 m 以上起降），载人任务按载货口径外推、段长余度收紧到 0.6，仅用于方案比较。")


class SitesFileError(RuntimeError):
    """站表读不到：这是系统故障，不是「没有合适站点」，两者结论必须分开。"""


# ==================== 站表装载 ====================

_DOC_CACHE: Dict[str, Any] = {"key": None, "doc": None}


def sites_file_path():
    return ROOT_DIR / settings.SITES_FILE


def load_sites_doc(force: bool = False) -> Dict[str, Any]:
    """读取根目录站表（带 mtime 缓存）。

    站表是**输入数据**不是配置：它决定哪些航段合法，所以进 git、和金标一样要求可复现。
    读不到时抛 SitesFileError，绝不用空表继续跑——那会把「文件缺失」伪装成「没有可用站点」。
    """
    path = sites_file_path()
    if not path.is_file():
        raise SitesFileError(f"未找到站表 {path}")
    st = path.stat()
    key = (st.st_mtime_ns, st.st_size)
    if not force and _DOC_CACHE["key"] == key and _DOC_CACHE["doc"] is not None:
        return _DOC_CACHE["doc"]
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    doc["_path"] = str(path)
    _DOC_CACHE["key"], _DOC_CACHE["doc"] = key, doc
    return doc


def public_sites(doc: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """给前端的站表视图：只留画图与 tooltip 需要的字段，出处全文不下发。"""
    if doc is None:
        doc = load_sites_doc()
    keep = ("id", "name", "type", "region", "lat", "lon", "elevation_m", "elev_kind",
            "geodatum", "hajin", "usable", "verify_priority")
    return {
        "stats": doc.get("stats", {}),
        "attribution": doc.get("attribution", ""),
        "aircraft": [{"key": k, "label": v["label"], "power": v["power"],
                      "certified": v["certified"], "cert": v["cert"],
                      "max_payload_kg": v["max_payload_kg"],
                      "cruise_kmh": v["cruise_kmh"], "cruise_note": v["cruise_note"]}
                     for k, v in AIRCRAFT_PACKS.items()],
        "sites": [{k: s.get(k) for k in keep if s.get(k) is not None}
                  for s in doc.get("sites", [])],
    }


# ==================== Pass 0：能力包门 ====================

def _range_km(pack: Dict[str, Any], payload_kg: float) -> Optional[float]:
    """载重 → 单程可飞距离（未扣余度）。锚点区间内线性内插，区间外返回 None（拒绝）。

    低于最轻锚点时**取该锚点值**而不是向下外推：轻载当然飞得更远，但厂家没给数，
    多给的一分都是我们凭空加的。
    """
    anchors: Sequence[Tuple[float, float]] = pack["range_anchors"]
    if payload_kg > anchors[-1][0]:
        return None
    if payload_kg <= anchors[0][0]:
        km = anchors[0][1]
    else:
        km = anchors[-1][1]
        for (w0, k0), (w1, k1) in zip(anchors, anchors[1:]):
            if payload_kg <= w1:
                f = (payload_kg - w0) / (w1 - w0) if w1 > w0 else 0.0
                km = k0 + f * (k1 - k0)
                break
    cap = pack.get("operational_cap_km")
    if cap:
        km = min(km, cap)
    return km


def leg_limit(pack_key: str, payload_kg: float, mission: str) -> Dict[str, Any]:
    """段长上限 = min(能源锚点折距, 运营口径) × 余度。任何拒绝都带独立 reason。"""
    pack = AIRCRAFT_PACKS[pack_key]
    margin = settings.SITE_LEG_MARGIN_CREW if mission == "crew" else settings.SITE_LEG_MARGIN_CARGO
    if payload_kg > float(pack["max_payload_kg"]):
        return {"reason": "payload_exceeds_max", "margin_k": margin, "leg_limit_km": 0.0,
                "message": (f"{pack['label']}：任务 {payload_kg:.0f} kg 装不下。")}
    km = _range_km(pack, payload_kg)
    if km is None:
        top = pack["range_anchors"][-1][0]
        return {"reason": "payload_beyond_published_envelope", "margin_k": margin,
                "leg_limit_km": 0.0,
                "message": (f"本机公开航程数据只覆盖到 {top:.0f} kg，"
                            f"{payload_kg:.0f} kg 档没有依据，不作答。")}
    return {"reason": None, "usable_km": round(km, 1), "margin_k": margin,
            "leg_limit_km": round(km * margin, 1), "message": ""}


def _site_elev(s: Dict[str, Any]) -> float:
    return float(s.get("elevation_m") or 0.0)


def _pool(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """可用站点池：海拔不参与筛站，只过 usable 这一道数据质量标记。"""
    return [s for s in doc.get("sites", []) if s.get("usable")]


# ==================== Pass 1：走廊 + 站点图 + 最少跳数 ====================

def _polyline(req: SitePlanRequest) -> List[Tuple[float, float]]:
    pts = [(float(req.start.lat), float(req.start.lon))]
    for w in req.waypoints or []:
        pts.append((float(w.lat), float(w.lon)))
    pts.append((float(req.end.lat), float(req.end.lon)))
    return pts


def _to_polyline_km(lat: float, lon: float, poly: Sequence[Tuple[float, float]]) -> float:
    """点到走廊折线的最短距离（局部等距投影，走廊尺度下误差远小于 150 km 外扩量）。"""
    kx = 111.0 * math.cos(math.radians(lat))
    px, py = lon * kx, lat * 111.0
    best = float("inf")
    for (a_lat, a_lon), (b_lat, b_lon) in zip(poly, poly[1:]):
        ax, ay = a_lon * kx, a_lat * 111.0
        bx, by = b_lon * kx, b_lat * 111.0
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
        d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
        best = min(best, d)
    return best


def _nodes(doc: Dict[str, Any], pool: List[Dict[str, Any]], req: SitePlanRequest,
           cap_km: float) -> List[Dict[str, Any]]:
    """走廊内的候选站点 + 起终点，构成图的节点。"""
    poly = _polyline(req)
    pad = float(settings.SITE_SCAN_PAD_KM)
    nodes = [{"key": "OD-start", "name": "起点", "lat": poly[0][0], "lon": poly[0][1],
              "elev": None, "site": None, "od": True},
             {"key": "OD-end", "name": "终点", "lat": poly[-1][0], "lon": poly[-1][1],
              "elev": None, "site": None, "od": True}]
    cands: List[Dict[str, Any]] = []
    for s in pool:
        lat, lon = float(s["lat"]), float(s["lon"])
        if _to_polyline_km(lat, lon, poly) > pad:
            continue
        if cap_km > 0:
            # 离两端都超过好几个段长的点，永远进不了任何解，先摘掉省得分量分析的噪声
            d0 = _haversine_km(lat, lon, poly[0][0], poly[0][1])
            d1 = _haversine_km(lat, lon, poly[-1][0], poly[-1][1])
            if d0 > cap_km * 6 and d1 > cap_km * 6:
                continue
        cands.append(s)
    for s in _dedup_close(cands, float(settings.SITE_MIN_SPACING_KM)):
        nodes.append({"key": s["id"], "name": s["name"], "lat": float(s["lat"]),
                      "lon": float(s["lon"]), "elev": _site_elev(s), "site": s,
                      "od": False})
    return nodes


def _dedup_close(sites_list: List[Dict[str, Any]], min_km: float) -> List[Dict[str, Any]]:
    """相距 < min_km 的点收敛成一个，**保留海拔更高（更保守）的那个**。

    站表层刻意不去重（保留原始信息），到算法层才收：`_plan_seq` 把每段端点 round 到
    2 位小数（≈1.1 km），两个相距 0.8 km 的候选进了 A* 就是同一个点，排序会莫名其妙。
    真实触发例子：西宁「城东区 2236 m / 城中区 2251 m」中心点相距 1.14 km。
    留高不留低，是为了不给出一条靠"两个其实同处一地的点"凑出来的假解。
    """
    kept: List[Dict[str, Any]] = []
    for s in sorted(sites_list, key=lambda x: -_site_elev(x)):
        if all(_haversine_km(float(s["lat"]), float(s["lon"]),
                             float(k["lat"]), float(k["lon"])) > min_km for k in kept):
            kept.append(s)
    return kept


def _adjacency(nodes: List[Dict[str, Any]], cap_km: float) -> List[List[Tuple[int, float]]]:
    n = len(nodes)
    adj: List[List[Tuple[int, float]]] = [[] for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = _haversine_km(nodes[i]["lat"], nodes[i]["lon"], nodes[j]["lat"], nodes[j]["lon"])
            if d <= cap_km:
                adj[i].append((j, d))
                adj[j].append((i, d))
    return adj


def _hop_min(nodes: List[Dict[str, Any]], adj: List[List[Tuple[int, float]]],
             si: int, ti: int) -> Dict[str, Any]:
    """字典序 (跳数, 直线里程) 最短路径。不写加权总分：权重说不出出处就不配进目标函数。

    第三级决胜（同跳同里程时按气象评分换位）放在 _swap_by_weather 里做一次局部替换，
    不塞进 Dijkstra —— 气象分不是可加量，塞进去就不满足松弛条件了。
    """
    best: Dict[int, Tuple[int, float]] = {si: (0, 0.0)}
    prev: Dict[int, int] = {}
    heap: List[Tuple[int, float, int]] = [(0, 0.0, si)]
    done: set = set()
    while heap:
        hops, dist, u = heapq.heappop(heap)
        if u in done:
            continue
        done.add(u)
        for v, w in adj[u]:
            cand = (hops + 1, dist + w)
            cur = best.get(v)
            if cur is not None and cur <= cand:
                continue
            best[v] = cand
            prev[v] = u
            heapq.heappush(heap, (cand[0], cand[1], v))
    reached = sorted(done)
    out: Dict[str, Any] = {"path": None, "reached": reached}
    if ti not in best:
        return out
    seq, u = [ti], ti
    while u != si:
        u = prev[u]
        seq.append(u)
    out["path"] = list(reversed(seq))
    out["hops"], out["straight_km"] = best[ti]
    return out


def _unreachable_report(nodes, adj, reached, ti, cap_km) -> Dict[str, Any]:
    """不连通时给「差多少接得上」，而不是回一个空数组装作没有解。"""
    goal = nodes[ti]
    best = None
    for i in reached:
        d = _haversine_km(nodes[i]["lat"], nodes[i]["lon"], goal["lat"], goal["lon"])
        if best is None or d < best[0]:
            best = (d, nodes[i])
    return {"component_size": len(reached), "total_nodes": len(nodes),
            "frontier_site": best[1]["name"] if best else "",
            "gap_km": round(best[0] - cap_km, 1) if best else None,
            "cap_km": round(cap_km, 1)}


# ==================== Pass 2：气象邻域统计（只排序与标注） ====================

def _corridor_bbox(nodes: List[Dict[str, Any]]) -> Tuple[float, float, float, float]:
    lats = [n["lat"] for n in nodes]
    lons = [n["lon"] for n in nodes]
    return (min(lats) - SEGMENT_PAD_DEG, max(lats) + SEGMENT_PAD_DEG,
            min(lons) - SEGMENT_PAD_DEG, max(lons) + SEGMENT_PAD_DEG)


def _neighborhood_stats(field, nodes: List[Dict[str, Any]], arrival_ns: Dict[str, Any],
                        radius_cells: int = 3) -> Tuple[Dict[str, Any], int]:
    """每个候选点邻域的 fly_ratio / worst_tke / nan_ratio。纯 numpy，一次算完。

    刻意不做硬过滤：一个点气象再好，机型起不来就是起不来；反之气象差也不该把
    一个唯一可用的县城摘掉——那是把统计噪声当物理墙。
    """
    tke, edr, lats, lons, times = field
    if tke is None:
        return {}, 0
    tke_a = np.asarray(tke, dtype=float)
    edr_a = np.asarray(edr, dtype=float)
    lat_a = np.asarray(lats, dtype=float)
    lon_a = np.asarray(lons, dtype=float)
    time_a = np.asarray(times, dtype='datetime64[ns]').astype('int64')
    scanned = int(tke_a.shape[-2] * tke_a.shape[-1]) if tke_a.ndim >= 3 else int(tke_a.size)
    out: Dict[str, Any] = {}
    mod_t = float(settings.TKE_RISK_MODERATE)
    mod_e = float(settings.EDR_RISK_MODERATE)
    for n in nodes:
        i = int(np.argmin(np.abs(lat_a - n["lat"])))
        j = int(np.argmin(np.abs(lon_a - n["lon"])))
        if abs(float(lat_a[i]) - n["lat"]) * 111.0 > 60.0 or \
           abs(float(lon_a[j]) - n["lon"]) * 111.0 * math.cos(math.radians(n["lat"])) > 60.0:
            continue    # 走廊外扩后仍取不到格点的点，不参与统计
        t_ns = arrival_ns.get(n["key"])
        if t_ns is None or time_a.size == 0:
            k = 0
        else:
            k = int(np.argmin(np.abs(time_a - int(t_ns))))
        sl = (slice(max(0, i - radius_cells), i + radius_cells + 1),
              slice(max(0, j - radius_cells), j + radius_cells + 1))
        win_t = tke_a[k, sl[0], sl[1]]
        win_e = edr_a[k, sl[0], sl[1]]
        finite = np.isfinite(win_t) & np.isfinite(win_e)
        cells = int(finite.size)
        good = int(np.count_nonzero(finite))
        if good == 0:
            out[n["key"]] = {"fly_ratio": None, "worst_tke": None, "nan_ratio": 1.0,
                             "cells": cells, "label": "无气象依据"}
            continue
        calm = finite & (win_t < mod_t) & (win_e < mod_e)
        fr = round(float(np.count_nonzero(calm)) / good, 3)
        out[n["key"]] = {
            "fly_ratio": fr,
            "worst_tke": round(float(np.nanmax(np.where(finite, win_t, np.nan))), 2),
            "nan_ratio": round(1.0 - good / cells, 3) if cells else 0.0,
            "cells": cells,
            "label": ("气象平稳" if fr >= 0.9 else
                      ("多数格点低于中风险下沿" if fr >= 0.6 else
                       "邻域有超过中风险下沿的格点")),
        }
    return out, scanned


def _swap_by_weather(nodes, adj, path: List[int], metrics: Dict[str, Any]) -> List[int]:
    """同一跳位上的并列候选按气象换一次：_swap_key 更小的同层站点换进来。

    只在「前后邻居都仍可达」的前提下换，且换进来的点不能已在序列里（防打转）——
    跳数与落地次数是用户真正付的代价，不能拿它去换一个更漂亮的邻域评分。
    邻接表已经编码了段长上限，所以这里不再判距离：能出现在 adj[] 里的边就是可达边。
    """
    if not metrics or len(path) < 3:
        return path
    cur = list(path)
    for pos in range(1, len(cur) - 1):
        prev_i, mid_i, next_i = cur[pos - 1], cur[pos], cur[pos + 1]
        best_i, best_key = mid_i, _swap_key(nodes[mid_i], metrics)
        for cand, _d_prev in adj[prev_i]:
            if cand in cur or cand == next_i:
                continue
            if not any(cc == next_i for cc, _d in adj[cand]):
                continue
            key = _swap_key(nodes[cand], metrics)
            if key < best_key:
                best_i, best_key = cand, key
        cur[pos] = best_i
    return cur


def _swap_key(node: Dict[str, Any], metrics: Dict[str, Any]):
    """越小越优。没有气象读数的点排在最后但**不被剔除**——缺测不等于更安全。"""
    m = metrics.get(node["key"])
    if not m or m.get("fly_ratio") is None:
        return (2, 1.0, 9999.0)
    if m.get("nan_ratio", 0) >= 0.8:
        return (1, 1.0, m.get("worst_tke") or 0.0)
    return (0, -m["fly_ratio"], m.get("worst_tke") or 0.0)


# ==================== Pass 3：逐段 A* 复验 + 编排 ====================

def _level_rank(level: str) -> int:
    try:
        return LEVEL_ORDER.index(level)
    except ValueError:
        return -1


def _stub_option(pack_key: str, payload_kg: float, mission: str) -> Dict[str, Any]:
    pack = AIRCRAFT_PACKS[pack_key]
    return {
        "aircraft": pack_key, "label": pack["label"], "certified": pack["certified"],
        "payload_kg": payload_kg, "mission": mission,
        "leg_limit_km": 0.0, "margin_k": 0.0,
        "feasible": False, "verdict": VERDICT_UNDETERMINED,
        "conclusion": VERDICT_LABELS[VERDICT_UNDETERMINED], "reason": None, "message": "",
        "legs": [], "relay_count": 0, "total_distance": 0.0, "total_time_h": None,
        "worst_level": "", "usable_sites": 0, "diagnostics": {},
        "search_effort": None,
        "notes": list(pack.get("notes") or []),
    }


def _reject(opt: Dict[str, Any], reason: str, message: str,
            diagnostics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把一次拒绝写成结论。reason → 四态的表在 schemas.SITE_REASON_VERDICT，别在这里即兴判断。"""
    opt["reason"] = reason
    opt["message"] = message
    opt["verdict"] = verdict_of(False, reason, SITE_REASON_VERDICT)
    opt["conclusion"] = VERDICT_LABELS[opt["verdict"]]
    opt["feasible"] = False
    if diagnostics:
        opt["diagnostics"].update(diagnostics)
    return opt


def _schedule(nodes: List[Dict[str, Any]], path: List[int], speed_kmh: float,
              base_ns: int) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """按直线里程推每跳的到达时刻（数据标签时间）。

    只用于选气象帧与展示预计时间；真实时刻以 Pass 3 逐段 A* 的结果为准。
    """
    cum = 0.0
    rows: List[Dict[str, Any]] = []
    ns_map: Dict[str, int] = {}
    for pos, idx in enumerate(path):
        if pos:
            a, b = nodes[path[pos - 1]], nodes[idx]
            cum += _haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
        ns = base_ns + int(cum / max(speed_kmh, 1e-6) * 3600 * 1_000_000_000)
        rows.append({"seq": pos, "cum_km": round(cum, 1), "arrival_ns": ns})
        ns_map[nodes[idx]["key"]] = ns
    return rows, ns_map


def _plan_for_aircraft(doc: Dict[str, Any], pack_key: str, req: SitePlanRequest,
                       payload_kg: float, deadline: Optional[float]) -> Dict[str, Any]:
    pack = AIRCRAFT_PACKS[pack_key]
    mission = (req.mission or "cargo").lower()
    opt = _stub_option(pack_key, payload_kg, mission)
    t0 = time.monotonic()
    # 缺省按该机能力包的巡航速度：段长本来就是「航时锚点 × 这个速度」折出来的，
    # 时间轴换一把尺，距离与航时就互相打脸。界面选机型时把速度框填成同值，显式改过才算覆盖。
    speed = float(req.speed) if req.speed else float(pack["cruise_kmh"])

    ll = leg_limit(pack_key, payload_kg, mission)
    opt["margin_k"] = ll["margin_k"]
    if ll["reason"]:
        return _reject(opt, ll["reason"], ll["message"])
    leg_cap = float(ll["leg_limit_km"])
    if req.leg_limit_km:
        leg_cap = float(req.leg_limit_km)
        opt["notes"].append(f"段长上限被手工覆盖为 {leg_cap:.0f} km（默认按能力包算得 {ll['leg_limit_km']:.0f} km）")
    opt["leg_limit_km"] = round(leg_cap, 1)
    edge_cap = leg_cap / max(float(settings.SITE_DETOUR_FACTOR), 1.0)

    pool = _pool(doc)
    opt["usable_sites"] = len(pool)
    opt["diagnostics"] = {
        "edge_cap_km": round(edge_cap, 1),
        "detour_factor": float(settings.SITE_DETOUR_FACTOR),
    }
    if mission == "crew":
        opt["notes"].append(CREW_BANNER)

    nodes = _nodes(doc, pool, req, edge_cap)
    if len(nodes) <= 2:
        return _reject(opt, "no_candidate_in_corridor",
                       "走廊内没有可用站点；把起降点挪近有场站的城镇，或调低任务重量换更长段长。",
                       {"corridor_nodes": len(nodes)})

    adj = _adjacency(nodes, edge_cap)
    res = _hop_min(nodes, adj, 0, 1)
    if res["path"] is None:
        report = _unreachable_report(nodes, adj, res["reached"], 1, edge_cap)
        return _reject(opt, "network_unreachable",
                       (f"可用站网不连通：起点这一侧只能到 {report['component_size']} 个站点，"
                        f"最靠前的 {report.get('frontier_site', '')} 距终点还差约 "
                        f"{report.get('gap_km')} km（建图段长 {edge_cap:.0f} km，"
                        f"= 段长上限 {leg_cap:.0f} km 折掉绕行余量）。"), report)

    base_data = np.datetime64(to_data_time_str(f"{req.date}T{req.start_time}"), 'ns')
    base_ns = int(base_data.astype('datetime64[ns]').astype('int64'))
    sched, ns_map = _schedule(nodes, res["path"], speed, base_ns)
    # bbox 与 hours 在这里定一次：Pass 2 的邻域统计与 Pass 3 的逐段复验共用这一份场
    bbox = _corridor_bbox(nodes)
    hours = int(min(max(math.ceil(sched[-1]["cum_km"] / max(speed, 1e-6)) + 2, 2), 31))
    field = None
    metrics: Dict[str, Any] = {}
    scanned = 0
    if req.weather_metrics:
        field = data_loader.read_turbulence_field(base_data, hours, bbox)
        if field is not None and field[0] is not None:
            scanned = int(np.asarray(field[0]).shape[-2] * np.asarray(field[0]).shape[-1])
            opt["diagnostics"]["field_shape"] = list(np.asarray(field[0]).shape)
            if scanned > int(settings.SITE_MAX_SCAN_CELLS):
                opt["notes"].append(f"走廊气象域 {scanned} 格超过上限 "
                                    f"{settings.SITE_MAX_SCAN_CELLS}，已跳过邻域标注")
                field = None
            else:
                metrics, _ = _neighborhood_stats(field, nodes, ns_map)
        else:
            opt["notes"].append("气象场读取为空（日期或走廊在数据覆盖之外），邻域标注缺失")
    path = _swap_by_weather(nodes, adj, res["path"], metrics)
    if path != res["path"]:
        sched, ns_map = _schedule(nodes, path, speed, base_ns)
        opt["diagnostics"]["weather_swaps"] = sum(1 for x, y in zip(res["path"], path) if x != y)
    opt["diagnostics"]["scanned_cells"] = scanned

    # --- Pass 3：逐段 A*。段数与墙钟双双封顶，超出的段标 reverified=false 而不是不报 ---
    legs: List[Dict[str, Any]] = []
    total_km = 0.0
    worst = ""
    verified = 0
    for pos in range(len(path) - 1):
        a, b = nodes[path[pos]], nodes[path[pos + 1]]
        straight = _haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
        leg: Dict[str, Any] = {
            "seq": pos + 1,
            "from": {"name": a["name"], "lat": round(a["lat"], 4), "lon": round(a["lon"], 4),
                     "site_id": a["key"], "elevation_m": a["elev"]},
            "to": {"name": b["name"], "lat": round(b["lat"], 4), "lon": round(b["lon"], 4),
                   "site_id": b["key"], "elevation_m": b["elev"]},
            "straight_km": round(straight, 1), "route_km": None, "flight_time_h": None,
            "over_range": False, "level": "", "reverified": False,
            "weather": metrics.get(b["key"]) or {},
        }
        if verified >= int(settings.SITE_MAX_LEGS):
            leg["note"] = f"段数上限 {settings.SITE_MAX_LEGS} 已到，未做 A* 复验（里程按直线计）"
        elif deadline is not None and time.monotonic() >= deadline:
            leg["note"] = "本次时间上限已到，未做 A* 复验（里程按直线计）"
        else:
            dep = to_user_time_str(np.datetime64(sched[pos]["arrival_ns"], 'ns'))
            leg_req = PlanRequest(
                start=Point(lat=a["lat"], lon=a["lon"]),
                end=Point(lat=b["lat"], lon=b["lon"]),
                waypoints=[], date=dep.split('T')[0], start_time=dep.split('T')[1],
                speed=speed, priority=req.priority or "normal")
            plan = plan_path_ex(leg_req, field=field, deadline=deadline)
            verified += 1
            opt["search_effort"] = search_effort_of(plan) or opt["search_effort"]
            if not (plan.found and plan.path):
                reason = plan.reason or "blocked"
                leg.update({"reverified": True, "feasible": False, "reason": reason,
                            "verdict": verdict_of(False, reason, PLANNER_REASON_VERDICT)})
                legs.append(leg)
                total_km += straight
                failed = leg
                break
            samples = [{"lat": float(p.lat), "lon": float(p.lon), "tke": t, "edr": e}
                       for p, t, e in zip(plan.path, plan.tkes, plan.edrs)]
            prof = build_risk_profile(samples)
            km = float(plan.total_distance)
            leg.update({"reverified": True, "route_km": round(km, 1),
                        "flight_time_h": round(km / max(speed, 1e-6), 2),
                        "level": prof.overall, "max_tke": prof.stats.max_tke,
                        "max_edr": prof.stats.max_edr, "n_nodes": len(plan.path),
                        "detour_ratio": round(km / straight, 3) if straight > 1e-6 else None,
                        "feasible": True})
            leg["over_range"] = bool(km > leg_cap + 1e-6)
            if leg["over_range"]:
                leg["note"] = f"复验实际 {km:.0f} km 超段长上限 {leg_cap:.0f} km（含绕行）"
            if _level_rank(prof.overall) > _level_rank(worst):
                worst = prof.overall
        legs.append(leg)
        total_km += float(leg["route_km"]) if leg.get("route_km") else leg["straight_km"]
    else:
        failed = None

    over = [l["seq"] for l in legs if l.get("over_range")]
    opt["legs"] = legs
    opt["relay_count"] = max(len(path) - 2, 0)
    opt["total_distance"] = round(total_km, 1)
    if failed is not None:
        return _reject(opt, failed.get("reason") or "blocked",
                       f"第 {failed['seq']} 段（{failed['from']['name']}→{failed['to']['name']}）"
                       f"A* 未给出安全航线。")
    if over:
        return _reject(opt, "detour_exceeds_threshold",
                       (f"第 {'、'.join(str(i) for i in over)} 段复验后实际里程超出段长上限："
                        "直线可达不等于飞得完，A* 绕开湍流要付里程。"),
                       {"over_range_legs": over})

    n_unverified = sum(1 for l in legs if not l.get("reverified"))
    opt.update({
        "feasible": True, "verdict": VERDICT_FEASIBLE,
        "conclusion": VERDICT_LABELS[VERDICT_FEASIBLE], "reason": None, "message": "",
        "total_time_h": round(total_km / max(speed, 1e-6), 2), "worst_level": worst,
    })
    if n_unverified:
        opt["notes"].append(f"有 {n_unverified} 段未做 A* 复验，总里程里那几段按直线计")
    opt["notes"].append("气象判据取自 500 hPa 高空场，且未参与地形与空域合法性校验")
    opt["diagnostics"]["elapsed_s"] = round(time.monotonic() - t0, 2)
    return opt


def plan_site_options(req: SitePlanRequest) -> Dict[str, Any]:
    """一次请求 = 一笔墙钟余额 + 一次读场，跑**一台**机型。

    双机对照已于 2026-09-28 撤销：海拔不再判站之后，两台在同一条走廊上给出的是同一条中继序列，
    差异只剩「谁在哪个载重档不答话」与一个航时刻度——两张并排的卡换来的是重复信息，
    而界面本来就决定不印型号。要比就换一台再点一次，两次请求各自完整复验。
    """
    doc = load_sites_doc()
    payload = float(req.payload_kg if req.payload_kg is not None
                    else settings.SITE_DEFAULT_PAYLOAD_KG)
    key = req.aircraft or settings.SITE_DEFAULT_AIRCRAFT
    if key not in AIRCRAFT_PACKS:
        raise ValueError(f"未知机型：{key}（可选：{'、'.join(AIRCRAFT_PACKS)}）")

    t0 = time.monotonic()
    deadline = search_deadline(settings.ASTAR_REQUEST_TIME_LIMIT_S)
    options = [_plan_for_aircraft(doc, key, req, payload, deadline)]
    return {
        "options": options,
        "meta": {
            "payload_kg": payload,
            "mission": (req.mission or "cargo"),
            "aircraft": key,
            "sites_total": len(doc.get("sites", [])),
            "sites_usable": sum(1 for s in doc.get("sites", []) if s.get("usable")),
            "site_stats": doc.get("stats", {}),
            "elapsed_s": round(time.monotonic() - t0, 2),
            "wall_clock_s": float(settings.ASTAR_REQUEST_TIME_LIMIT_S),
        },
    }


# ==================== 自然语言任务解析（一期＝半自动，绝不直接触发 A*） ====================

_RULE_MISSION = {
    "crew": ("载人", "乘客", "旅客", "载人", "担架", "伤员", "救护", "送人"),
    "cargo": ("货", "物资", "运", "送", "补给", "邮件", "药材", "兽药", "建材", "氧气瓶"),
}
_RULE_WEIGHT = r"(\d+(?:\.\d+)?)\s*(公斤|kg|KG|Kg|千克|吨|t|T)"


def _rule_parse(text: str) -> Dict[str, Any]:
    """确定性兜底解析：只认得出来的字段，认不出的一律留空，不猜。

    与 assessor 的规则版报告同一个道理 —— LLM 不可用时演示不能塌，
    但兜底层**不许发明**参数：留空由界面提示用户填，编一个载重就是拿演示骗自己。
    """
    out: Dict[str, Any] = {"payload_kg": None, "aircraft": None, "mission": None,
                           "start_name": None, "end_name": None, "matched": []}
    t = (text or "").strip()
    if not t:
        return out
    import re

    m = re.search(_RULE_WEIGHT, t)
    if m:
        val = float(m.group(1))
        unit = m.group(2)
        val *= 1000.0 if unit in ("吨", "t", "T") else 1.0
        out["payload_kg"] = val
        out["matched"].append("payload")

    if any(k in t for k in _RULE_MISSION["crew"]):
        out["mission"] = "crew"
        out["matched"].append("mission=crew")
    elif any(k in t for k in _RULE_MISSION["cargo"]):
        out["mission"] = "cargo"
        out["matched"].append("mission=cargo")

    if any(k in t for k in ("电动", "纯电", "峰飞", "V2000", "eVTOL")):
        out["aircraft"] = "v2000cg"
        out["matched"].append("aircraft=v2000cg")
    elif any(k in t for k in ("油动", "燃油", "直升机", "TD550", "共轴")):
        out["aircraft"] = "td550"
        out["matched"].append("aircraft=td550")

    names = [str(c.get("name")) for c in geo.CITIES]
    found = [n for n in names if n and n in t]
    if len(found) >= 2:
        # 出现顺序即方向：中文句式「从 A 到 B」「A 送 B」都以 A 在前
        out["start_name"], out["end_name"] = found[0], found[-1]
        out["matched"].append("od")
    elif len(found) == 1:
        out["end_name"] = found[0]
        out["matched"].append("od=partial")
    return out


_ALLOWED_FIELDS = ("payload_kg", "aircraft", "mission", "start_name", "end_name")


def _llm_parse(text: str) -> Optional[Dict[str, Any]]:
    """让模型只做「一句话 → 五个字段」的抽取，机型门限与航程一律不进 prompt。

    复用 assessor 的三条纪律：白名单字段、口径不交给模型、任何异常都降级到规则版。
    """
    if not assessor.llm_available():
        return None
    import json as _json
    import urllib.request

    prompt = (
        "你是无人机任务参数抽取器。只输出一个 JSON 对象，不要解释、不要 Markdown。\n"
        "字段与取值范围：\n"
        '  "payload_kg": 数字或 null（任务重量，单位公斤）\n'
        '  "aircraft": "td550" | "v2000cg" | null\n'
        '  "mission": "cargo" | "crew" | null\n'
        '  "start_name": 字符串或 null（出发地地名，照抄原文）\n'
        '  "end_name": 字符串或 null（目的地地名，照抄原文）\n'
        "原文没有说到的字段必须填 null，禁止推测、禁止补全默认值。\n"
        "已知的可选地名：" + "、".join(str(c.get("name")) for c in geo.CITIES) + "\n"
        "任务描述：" + text
    )
    body = {
        "model": settings.AI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 200,
    }
    key = os.environ.get(settings.AI_API_KEY_ENV or "UAV_AI_API_KEY", "")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    url = settings.AI_BASE_URL.rstrip("/") + "/chat/completions"
    try:
        with urllib.request.urlopen(urllib.request.Request(
                url, data=_json.dumps(body).encode("utf-8"),
                headers=headers, method="POST"),
                timeout=float(settings.AI_TIMEOUT_S)) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"] or ""
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            return None
        raw = _json.loads(content[start:end + 1])
    except Exception:
        return None
    out: Dict[str, Any] = {}
    for f in _ALLOWED_FIELDS:
        v = raw.get(f)
        if f == "payload_kg":
            try:
                v = float(v) if v is not None else None
            except (TypeError, ValueError):
                v = None
        elif f == "aircraft":
            v = v if v in AIRCRAFT_PACKS else None
        elif f == "mission":
            v = v if v in ("cargo", "crew") else None
        else:
            v = str(v) if v else None
        out[f] = v
    return out


def parse_task(req: "TaskParseRequest") -> Dict[str, Any]:
    """自然语言 → 表单预填。返回里必带 source，界面据此显示「AI 解析 / 规则解析」徽标。"""
    rule = _rule_parse(req.text)
    if req.use_llm and settings.AI_ENABLED:
        got = _llm_parse(req.text)
        if got is not None:
            merged = dict(rule)
            for f in _ALLOWED_FIELDS:
                if got.get(f) is not None:
                    merged[f] = got[f]
            merged["matched"] = rule.get("matched", [])
            return {"fields": merged, "rule_fields": rule, "source": "llm"}
    return {"fields": rule, "rule_fields": rule, "source": "rule"}
