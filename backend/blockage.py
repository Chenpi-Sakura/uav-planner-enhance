"""
阻塞区域分析：识别导致航线不可行的「条带状 / 块状」高危湍流区。

思路：
  1. 在起降点包围盒内构建危险掩膜 M = (TKE >= 硬阈值) | (EDR >= 硬阈值)；
  2. 用 scipy.ndimage.label 求连通域，得到各危险区团块；
  3. 保留与 A-B 直线走廊相交、且面积达标的团块；
  4. 提取面积、形状（PCA 长宽比：条带状 / 块状）、强度峰值与中心位置。

时间口径（重要）：
  规划是 4D 的，障碍物随时刻变化。若对整日 24 个小时做平均，
  会把「只在这一趟飞行窗口内出现」的危险区稀释掉，导致报出的
  阻塞区既不在走廊上、也不是真正封路的那一块。
  因此这里按【飞行窗口内各时刻危险区的并集】计算危险掩膜：
  只要某格点在窗口内任一时刻达到硬阈值，就视为该格点会阻塞航线。
"""
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional, Tuple
import math

import numpy as np
import scipy.ndimage as ndimage

from config import settings

SHAPE_BAND = "条带状"
SHAPE_BLOB = "块状"

# 长宽比阈值：>= 该值判定为条带状
BAND_RATIO = 3.0

# 危险区到走廊的最小距离 <= 该值（km）即视为「横亘航线走廊」
CORRIDOR_HIT_KM = 10.0


@dataclass
class BlockingRegion:
    """一个阻塞性危险区域。"""
    center: Tuple[float, float]          # (lat, lon)
    radius_km: float                     # 等效半径（面积等效）
    area_km2: float
    shape: str                           # 条带状 / 块状
    aspect_ratio: float
    max_tke: float
    max_edr: float
    mean_tke: float
    crosses_corridor: bool
    corridor_distance_km: float          # 中心到 A-B 直线的最短距离
    polygon: List[List[float]] = None    # 轮廓点（经纬度），供地图高亮


def analyze_blockage(tke_grid: np.ndarray,
                     edr_grid: np.ndarray,
                     lats: np.ndarray,
                     lons: np.ndarray,
                     start: Tuple[float, float],
                     end: Tuple[float, float],
                     tke_thresh: Optional[float] = None,
                     edr_thresh: Optional[float] = None,
                     min_area_km2: Optional[float] = None,
                     top_n: Optional[int] = None,
                     times: Optional[np.ndarray] = None,
                     base_time=None,
                     window_hours: Optional[float] = None) -> List[BlockingRegion]:
    """识别阻塞航线的高危湍流区。

    参数中的 tke_grid / edr_grid 形如 (lat, lon)，与 lats / lons 一一对应；
    若为 (time, lat, lon)，则按 times / base_time / window_hours 指定的
    飞行窗口做【危险区并集】（见模块 docstring 的时间口径说明）。

    返回按「阻塞性」排序的区域列表（最多 top_n 个）。
    """
    tke_thresh = tke_thresh if tke_thresh is not None else settings.TKE_THRESHOLD_HIGH
    edr_thresh = edr_thresh if edr_thresh is not None else settings.EDR_THRESHOLD_HIGH
    min_area = min_area_km2 if min_area_km2 is not None else settings.BLOCKAGE_MIN_AREA_KM2
    top_n = top_n if top_n is not None else settings.BLOCKAGE_TOP_N

    if tke_grid is None or edr_grid is None or lats is None or lons is None:
        return []

    tke = np.asarray(tke_grid, dtype=float)
    edr = np.asarray(edr_grid, dtype=float)
    lats = np.asarray(lats, dtype=float)
    lons = np.asarray(lons, dtype=float)

    if tke.ndim == 3:
        # (time, lat, lon) -> 取飞行窗口内各时刻危险区的并集
        window_slices = _select_window_slices(times, base_time, window_hours, tke.shape[0])
        tke_win = tke[window_slices]
        edr_win = edr[window_slices]

        danger_win = (
            (np.nan_to_num(tke_win, nan=-1.0) >= tke_thresh)
            | (np.nan_to_num(edr_win, nan=-1.0) >= edr_thresh)
        )
        dangerous = danger_win.any(axis=0)

        # 峰值取窗口内最大值（忽略全 NaN 切片，避免 All-NaN 警告）
        tke = _nanmax(tke_win, axis=0)
        edr = _nanmax(edr_win, axis=0)
    else:
        if tke.ndim != 2:
            return []
        dangerous = (np.nan_to_num(tke, nan=-1.0) >= tke_thresh) | (np.nan_to_num(edr, nan=-1.0) >= edr_thresh)

    if tke.shape != edr.shape or tke.ndim != 2:
        return []

    if not dangerous.any():
        return []

    labels, count = ndimage.label(dangerous)
    if count == 0:
        return []

    # 每个格点的等效面积（km²）
    if len(lats) > 1:
        dlat_km = abs(lats[1] - lats[0]) * 111.0
    else:
        dlat_km = 5.0
    lat_mid = math.radians(float(np.mean(lats)))
    if len(lons) > 1:
        dlon_km = abs(lons[1] - lons[0]) * 111.0 * math.cos(lat_mid)
    else:
        dlon_km = 5.0
    cell_km2 = max(dlat_km * dlon_km, 1e-6)

    regions: List[BlockingRegion] = []

    for lab in range(1, count + 1):
        mask = labels == lab
        area_km2 = float(mask.sum()) * cell_km2
        if area_km2 < min_area:
            continue

        idx_lat, idx_lon = np.nonzero(mask)
        reg_lats = lats[idx_lat]
        reg_lons = lons[idx_lon]

        center_lat = float(np.mean(reg_lats))
        center_lon = float(np.mean(reg_lons))

        # 形状：PCA 主轴 / 短轴（在 km 空间做，避免经度形变）
        pts = np.column_stack([
            (reg_lons - center_lon) * 111.0 * math.cos(lat_mid),
            (reg_lats - center_lat) * 111.0,
        ])
        aspect, extent_km = _pca_aspect(pts)
        shape = SHAPE_BAND if aspect >= BAND_RATIO else SHAPE_BLOB
        radius_km = math.sqrt(area_km2 / math.pi)

        reg_tke = tke[mask]
        reg_edr = edr[mask]
        max_tke = float(_nanmax(reg_tke, axis=0)) if reg_tke.size else 0.0
        max_edr = float(_nanmax(reg_edr, axis=0)) if reg_edr.size else 0.0
        with np.errstate(invalid="ignore"):
            mean_tke = float(np.nanmean(reg_tke)) if reg_tke.size else 0.0
        if not np.isfinite(mean_tke):
            mean_tke = 0.0

        # 与走廊的距离：取【区域内格点到 A-B 线段的最小距离】，
        # 而不是质心距离。大范围危险区的质心可能远离走廊，
        # 但它的边缘其实横亘在航路上，用质心判断会得出「未阻塞」的错误结论。
        corridor_km = _min_corridor_distance(reg_lats, reg_lons, start, end, floor_km=0.1)
        crosses = corridor_km <= CORRIDOR_HIT_KM

        outline = _outline_polygon(mask, lats, lons)

        regions.append(BlockingRegion(
            center=(round(center_lat, 4), round(center_lon, 4)),
            radius_km=round(radius_km, 1),
            area_km2=round(area_km2, 1),
            shape=shape,
            aspect_ratio=round(aspect, 2),
            max_tke=round(max_tke, 4),
            max_edr=round(max_edr, 4),
            mean_tke=round(mean_tke, 4),
            crosses_corridor=bool(crosses),
            corridor_distance_km=round(corridor_km, 1),
            polygon=outline,
        ))

    # 阻塞性排序：先看是否横亘走廊，再比面积与强度
    regions.sort(key=lambda r: (
        not r.crosses_corridor,
        -r.area_km2,
        -r.max_tke,
    ))

    return regions[:top_n]


def _min_corridor_distance(reg_lats: np.ndarray,
                           reg_lons: np.ndarray,
                           start: Tuple[float, float],
                           end: Tuple[float, float],
                           floor_km: float = 0.1,
                           max_cells: int = 4000) -> float:
    """区域内所有格点到 A-B 线段的最小距离（km）。

    大区域会先做等间隔抽样，避免逐格点计算带来的额外耗时。
    """
    n = int(reg_lats.size)
    if n == 0:
        return float("inf")

    if n > max_cells:
        step = int(math.ceil(n / float(max_cells)))
        reg_lats = reg_lats[::step]
        reg_lons = reg_lons[::step]

    best = float("inf")
    for la, lo in zip(reg_lats, reg_lons):
        d = _point_to_segment_km(float(la), float(lo), start, end)
        if d < best:
            best = d
            if best <= floor_km:
                break
    return best


def _nanmax(arr: np.ndarray, axis: int) -> np.ndarray:
    """忽略 NaN 的最大值，且不产生 All-NaN slice 警告。"""
    filled = np.where(np.isnan(arr), -np.inf, arr)
    out = filled.max(axis=axis)
    return np.where(np.isfinite(out), out, np.nan)


def _select_window_slices(times: Optional[np.ndarray],
                          base_time,
                          window_hours: Optional[float],
                          n_slices: int) -> np.ndarray:
    """挑选飞行窗口覆盖的时间切片下标。

    - times / base_time 缺失时退化为「全部切片」；
    - window_hours 缺失时默认 3 小时（覆盖典型短途任务）；
    - 若窗口内一个切片都没有，则退化为距离 base_time 最近的那一个。
    """
    all_idx = np.arange(n_slices)
    if times is None or base_time is None:
        return all_idx

    hours = float(window_hours) if window_hours is not None else 3.0
    try:
        t = np.asarray(times, dtype="datetime64[ns]")
        t0 = np.datetime64(base_time).astype("datetime64[ns]")
        t1 = t0 + np.timedelta64(int(round(hours * 3600)), "s")
        sel = np.nonzero((t >= t0) & (t <= t1))[0]
        if sel.size == 0:
            sel = np.array([int(np.argmin(np.abs(t - t0)))])
        return sel
    except Exception:
        return all_idx


def _pca_aspect(pts: np.ndarray) -> Tuple[float, float]:
    """PCA 长宽比与主轴跨度（km）。"""
    if pts.shape[0] < 3:
        return 1.0, 0.0
    centered = pts - pts.mean(axis=0, keepdims=True)
    try:
        cov = np.cov(centered.T)
        eigvals, eigvecs = np.linalg.eigh(cov)
    except np.linalg.LinAlgError:
        return 1.0, 0.0

    order = np.argsort(eigvals)[::-1]
    major = max(float(eigvals[order[0]]), 1e-9)
    minor = max(float(eigvals[order[1]]), 1e-9)
    aspect = math.sqrt(major / minor)

    proj = centered @ eigvecs[:, order[0]]
    extent = float(proj.max() - proj.min())
    return aspect, extent


def _point_to_segment_km(plat: float, plon: float,
                         a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """点到线段的最短距离（km），在等距圆柱近似下计算。"""
    lat0 = math.radians((a[0] + b[0]) / 2.0)
    kx = 111.0 * math.cos(lat0)
    ky = 111.0

    ax, ay = a[1] * kx, a[0] * ky
    bx, by = b[1] * kx, b[0] * ky
    px, py = plon * kx, plat * ky

    dx, dy = bx - ax, by - ay
    seg_len2 = dx * dx + dy * dy
    if seg_len2 <= 1e-9:
        return math.hypot(px - ax, py - ay)

    t = ((px - ax) * dx + (py - ay) * dy) / seg_len2
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def _outline_polygon(mask: np.ndarray, lats: np.ndarray, lons: np.ndarray,
                     max_points: int = 40) -> List[List[float]]:
    """提取连通域外轮廓（经纬度点列），并抽样到 max_points 以内。"""
    try:
        filled = ndimage.binary_fill_holes(mask)
        eroded = ndimage.binary_erosion(filled, iterations=1)
    except Exception:
        return []

    boundary = filled & ~eroded
    idx_lat, idx_lon = np.nonzero(boundary)
    if len(idx_lat) == 0:
        return []

    # 按角度排序形成闭合折线
    clat = float(np.mean(lats[idx_lat]))
    clon = float(np.mean(lons[idx_lon]))
    angles = np.arctan2(lats[idx_lat] - clat, lons[idx_lon] - clon)
    order = np.argsort(angles)

    pts: List[List[float]] = []
    step = max(1, len(order) // max_points)
    for k in order[::step]:
        pts.append([round(float(lats[idx_lat[k]]), 4), round(float(lons[idx_lon[k]]), 4)])

    if len(pts) >= 3:
        pts.append(pts[0])  # 闭合
    return pts


def to_dict(regions: List[BlockingRegion]) -> List[Dict[str, Any]]:
    return [asdict(r) for r in regions]


def dominant_shape(regions: List[BlockingRegion]) -> Optional[str]:
    """返回最主要阻塞区的形状描述（用于文案）。"""
    blocking = [r for r in regions if r.crosses_corridor] or regions
    if not blocking:
        return None
    return blocking[0].shape