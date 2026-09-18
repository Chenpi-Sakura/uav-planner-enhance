"""
地理信息与城市地名表。

用途：
  1. 「区域飞行等级」分段 —— 以重要城市/县市为间隔点切分航线；
  2. AI 评估报告的地理叙事 —— 把经纬度翻译成地名；
  3. 替代起降点建议 —— 以基地为锚点给出可读方案。

城市表为青藏高原区域内代表性城镇（经纬度取城区近似值，
精度对本平台的 5km 级评估足够）。
"""
from typing import List, Dict, Optional
import math

# (name, region, lat, lon)
CITIES: List[Dict[str, object]] = [
    {"name": "拉萨", "region": "西藏", "lat": 29.6500, "lon": 91.1300},
    {"name": "日喀则", "region": "西藏", "lat": 29.2700, "lon": 88.8800},
    {"name": "林芝", "region": "西藏", "lat": 29.6600, "lon": 94.3600},
    {"name": "昌都", "region": "西藏", "lat": 31.1500, "lon": 97.1800},
    {"name": "那曲", "region": "西藏", "lat": 31.4800, "lon": 92.0500},
    {"name": "山南", "region": "西藏", "lat": 29.2400, "lon": 91.7700},
    {"name": "江孜", "region": "西藏", "lat": 28.9200, "lon": 89.6000},
    {"name": "当雄", "region": "西藏", "lat": 30.4800, "lon": 91.1000},
    {"name": "尼木", "region": "西藏", "lat": 29.4300, "lon": 90.1600},
    {"name": "工布江达", "region": "西藏", "lat": 29.8900, "lon": 93.2500},
    {"name": "墨竹工卡", "region": "西藏", "lat": 29.8400, "lon": 91.7300},
    {"name": "曲水", "region": "西藏", "lat": 29.4900, "lon": 90.7400},
    {"name": "浪卡子", "region": "西藏", "lat": 28.9700, "lon": 90.4000},
    {"name": "林周", "region": "西藏", "lat": 30.0500, "lon": 91.2600},
    {"name": "类乌齐", "region": "西藏", "lat": 31.2100, "lon": 96.6000},
]

# 用于「夹点判断」的走廊半径：航线经过城市时，取该半径为归属范围
CORRIDOR_KM = 25.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def nearest_city(lat: float, lon: float) -> Dict[str, object]:
    """返回距离给定坐标最近的城市及其距离（km）。"""
    best = None
    best_d = float('inf')
    for c in CITIES:
        d = _haversine_km(lat, lon, float(c["lat"]), float(c["lon"]))
        if d < best_d:
            best_d = d
            best = c
    return {"name": best["name"], "region": best["region"], "distance_km": round(best_d, 1)} if best else None


def describe_point(lat: float, lon: float, max_km: float = 80.0) -> str:
    """把坐标翻译成中文地名描述（用于评估报告）。

    命中范围内返回「拉萨附近」这类表述；超出范围则给出方位与距离。
    """
    nc = nearest_city(lat, lon)
    if not nc:
        return f"({lat:.2f}, {lon:.2f})"
    d = nc["distance_km"]
    if d <= 15:
        return f"{nc['name']}市区附近"
    if d <= max_km:
        direction = _bearing_label(lat, lon, _city_by_name(nc["name"]))
        return f"{nc['name']}以{direction}约{d:.0f}km"
    return f"{nc['name']}西南方向偏远区域" if lat < 30 else f"{nc['name']}以北约{d:.0f}km"


def _city_by_name(name: str) -> Dict[str, object]:
    for c in CITIES:
        if c["name"] == name:
            return c
    return CITIES[0]


def _bearing_label(lat: float, lon: float, city: Dict[str, object]) -> str:
    """粗略方位：以城市为参照，判断该点位于城市的哪个方向。"""
    dlat = lat - float(city["lat"])
    dlon = lon - float(city["lon"])
    if abs(dlat) >= abs(dlon):
        return "北" if dlat >= 0 else "南"
    return "东" if dlon >= 0 else "西"


def cities_on_path(path: List[Dict[str, float]], max_km: float = CORRIDOR_KM) -> List[Dict[str, object]]:
    """找出航线沿途（走廊范围内）途经的城市，按沿程顺序返回。

    参数 path 元素需含 'lat'/'lon'（可选 'cum_km'）。
    """
    if not path:
        return []

    hits: List[Dict[str, object]] = []
    for c in CITIES:
        best_i = -1
        best_d = float('inf')
        for i, p in enumerate(path):
            d = _haversine_km(float(p["lat"]), float(p["lon"]), float(c["lat"]), float(c["lon"]))
            if d < best_d:
                best_d = d
                best_i = i
        if best_d <= max_km and best_i >= 0:
            pos = float(path[best_i].get("cum_km", best_i))
            hits.append({"name": c["name"], "region": c["region"], "index": best_i, "distance_km": round(best_d, 1), "pos": pos})

    hits.sort(key=lambda h: h["pos"])
    return hits


def build_segment_bounds(path: List[Dict[str, float]]) -> List[Dict[str, object]]:
    """构造「以重要城市为间隔点」的分段边界。

    - 起点与终点始终作为首尾边界；
    - 途经城市作为中间间隔点；
    - 返回元素含 from_name / to_name / start_index / end_index。
    """
    if not path:
        return []

    n = len(path)
    anchors: List[Dict[str, object]] = []

    start_desc = describe_point(float(path[0]["lat"]), float(path[0]["lon"]))
    anchors.append({"name": start_desc, "index": 0})

    for hit in cities_on_path(path):
        idx = int(hit["index"])
        # 避免与起点/终点锚点过近
        if idx <= 1 or idx >= n - 2:
            continue
        if anchors and idx - int(anchors[-1]["index"]) < 2:
            continue
        anchors.append({"name": f"{hit['name']}", "index": idx})

    end_desc = describe_point(float(path[-1]["lat"]), float(path[-1]["lon"]))
    anchors.append({"name": end_desc, "index": n - 1})

    bounds: List[Dict[str, object]] = []
    for i in range(len(anchors) - 1):
        a = int(anchors[i]["index"])
        b = int(anchors[i + 1]["index"])
        if b <= a:
            continue
        bounds.append({
            "from_name": anchors[i]["name"],
            "to_name": anchors[i + 1]["name"],
            "start_index": a,
            "end_index": b,
        })
    return bounds