"""气象实况取数：按地名在指定时刻取真实的 TKE / EDR / 风温湿。

存在的理由：对话层的防幻觉纪律是「只允许引用 JSON 里的数值」，而既有上下文里
只有评估结果 —— 用户问「拉萨现在 TKE 是多少」时，模型手里**没有任何数**，
只能答一句空话。这里补上那个数：真的去气象场里取，取到什么给什么。

两条硬纪律：
1. 缺测就是缺测。``data_loader`` 在数据文件缺失时会退回占位常量（tke/edr = 0），
   那会让「没有数据」显示成「风平浪静」—— 比报错危险得多，所以取数前先查
   ``_loaded``，取数后逐点判 NaN，缺测点如实报「无数据」。
2. 风险等级一律走 ``risk.classify_point``，与 A* 判可飞、剖面判分段同源。
   这里自己另写一套阈值判断，界面上的等级就会和航线上的等级对不上。
"""

import math
from typing import Any, Dict, List, Optional

import numpy as np

from config import settings
from data_loader import data_loader
from risk import classify_point
from timebase import to_data_time_str

# 数据集是 500 hPa 单层（见 data_loader.read_slab 里 ``levs == 500.0`` 的取法），
# 约合 5 km 高度、对流层中层。这个数字必须随结果一起透出：把 500 hPa 的温度说成
# 「气温」，用户会拿它当地面温度看（拉萨 7 月地面十几度，而这里是零下几度）。
# 数值口径与报告、时间窗口、多航点完全一致（都读同一个 slab）。
DATA_LEVEL_HPA = 500
DATA_LEVEL_NOTE = "500 hPa 等压面（约 5 km 高度，对流层中层），不是地面观测"


def _f(value: Any) -> Optional[float]:
    """NaN -> None。JSON 里的 null 就是「这一格没测到」，不能变成 0。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else round(v, 4)


def _outside_coverage(lat: float, lon: float) -> bool:
    """超出气象数据覆盖框的点直接判缺测：查表只会拿到边界外的垃圾值。"""
    bbox = data_loader.coverage_bbox()
    if not bbox:
        return False
    return not (bbox["lat_min"] <= lat <= bbox["lat_max"]
                and bbox["lon_min"] <= lon <= bbox["lon_max"])


def _is_placeholder(u: Any, v: Any, w: Any, tc: Any) -> bool:
    """识别 ``get_meteorology_at_points`` 在 slab 读取失败时退回的占位常量。

    这不是杞人忧天：``data_loader`` 里 ``_gather_slab`` 返回 None 时直接
    ``return out``，而 ``out`` 是 ``{u:5, v:5, w:0, tc:280, rh:50, tke:0, edr:0}``
    的占位字典 —— **不是 NaN**。本模块开头那道 ``_loaded`` 守卫挡不住这种情况
    （数据确实加载好了，只是这一次 slab 没读到），于是 tke=0 会经
    ``classify_point`` 判成「低风险」，把一次读盘失败说成「风平浪静，可以飞」。

    真实格点不会同时精确等于这组常数，所以整体比对足够可靠；宁可误报「无数据」，
    也不能误报「安全」。
    """
    try:
        return (float(u) == 5.0 and float(v) == 5.0 and float(w) == 0.0
                and float(tc) == 280.0)
    except (TypeError, ValueError):
        return False


def query_weather(points: List[Dict[str, Any]], date: str, time: str) -> Dict[str, Any]:
    """在 ``date time``（北京时间）这个时刻，取每个点的实况。

    ``points`` 每项 ``{name, lat, lon}``。所有点取**同一时刻** —— 这是「此刻天气」
    的语义，与沿程剖面「每点按自己的到达时刻取」的 4D 口径刻意不同，别混用。
    """
    points = [p for p in (points or []) if p.get("lat") is not None and p.get("lon") is not None]
    if not points:
        return {"available": False, "reason": "没有可查询的地点", "points": []}

    # 数据没加载成功时绝不去取：get_meteorology_at_points 会自己 load_data()，
    # 而 load_data 失败时它仍返回 tke=0 的占位值 —— 那会把「没数据」说成「零湍流」。
    if not getattr(data_loader, "_loaded", False):
        return {
            "available": False,
            "reason": "气象数据尚未加载完成，暂时查不到实况。",
            "points": [],
        }

    lats = [float(p["lat"]) for p in points]
    lons = [float(p["lon"]) for p in points]
    user_time = f"{date}T{time or '08:00'}"
    try:
        data_time = to_data_time_str(user_time)
    except (ValueError, TypeError):
        return {"available": False, "reason": f"时间 {user_time} 无法解析", "points": []}

    # 每个点查自己（时刻相同，所以 times 只有一个值广播过去）
    times = [data_time] * len(lats)
    batch = data_loader.get_meteorology_at_points(lats, lons, times)

    out: List[Dict[str, Any]] = []
    for i, p in enumerate(points):
        lat, lon = lats[i], lons[i]
        row: Dict[str, Any] = {"name": p.get("name") or "该点", "lat": lat, "lon": lon}

        if _outside_coverage(lat, lon):
            out.append({**row, "available": False, "note": "该点超出气象数据覆盖范围"})
            continue

        tke = _f(batch["tke"][i])
        edr = _f(batch["edr"][i])
        u, v = _f(batch["u"][i]), _f(batch["v"][i])
        w = _f(batch["w"][i])
        tc = _f(batch["tc"][i])

        if _is_placeholder(u, v, w, tc):
            out.append({**row, "available": False, "note": "该时刻该点未读到气象数据"})
            continue
        if tke is None or edr is None:
            out.append({**row, "available": False, "note": "该时刻该点缺测"})
            continue

        # tc 的长名就是 "Temperature (C)"：数据集给的是摄氏度，不是开尔文。
        # 按 K 换算会得到 -278℃ 这种一眼假的数，不能凭「温度通常存 K」去猜单位。
        rh = _f(batch["rh"][i])
        qv = _f(batch["qvapor"][i])

        out.append({
            **row,
            "available": True,
            "tke": tke,
            "edr": edr,
            # 水平风速模长 |V| = sqrt(u² + v²)；单位 m/s（data_loader 的口径）
            "wind_speed_ms": round(math.hypot(u, v), 4) if (u is not None and v is not None) else None,
            "u": u,
            "v": v,
            "w": w,
            "temp_c": tc,
            "rh_pct": rh,
            "qvapor": qv,
            "level": classify_point(tke, edr),
        })

    return {
        "available": True,
        "query_time": user_time,
        "timezone": "北京时间（UTC+8）",
        "level_hpa": DATA_LEVEL_HPA,
        "level_note": DATA_LEVEL_NOTE,
        "points": out,
        "thresholds": {
            "tke": {
                "moderate": settings.TKE_RISK_MODERATE,
                "high": settings.TKE_RISK_HIGH,
                "unflyable": settings.TKE_THRESHOLD_HIGH,
            },
            "edr": {
                "moderate": settings.EDR_RISK_MODERATE,
                "high": settings.EDR_RISK_HIGH,
                "unflyable": settings.EDR_THRESHOLD_HIGH,
            },
            "note": "TKE 与 EDR 取较高的一档作为该点等级；不可飞为硬约束。",
        },
    }
