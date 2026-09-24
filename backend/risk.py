"""
风险分级与航线风险统计。

阈值口径与 planner.py 的软/硬约束保持一致（脉动方差刻度，2026-09-23 重标定）：
  - 安全（低风险）：  TKE < 8    且 EDR < 0.208
  - 中风险：        TKE >= 8    或 EDR >= 0.208
  - 高风险：        TKE >= 16   或 EDR >= 0.295
  - 完全没法飞（硬约束）：TKE >= 26 或 EDR >= 0.376
"""
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
import math

from config import settings

# 风险等级（由低到高）
LEVEL_LOW = "低风险"
LEVEL_MODERATE = "中风险"
LEVEL_HIGH = "高风险"
LEVEL_UNFLYABLE = "不可飞"

LEVEL_ORDER = [LEVEL_LOW, LEVEL_MODERATE, LEVEL_HIGH, LEVEL_UNFLYABLE]


def classify_point(tke: float, edr: float,
                   tke_high: Optional[float] = None,
                   edr_high: Optional[float] = None) -> str:
    """对单个采样点做风险分级。

    取 TKE / EDR 两个维度中的较高等级作为该点风险等级。
    """
    tke_high = tke_high if tke_high is not None else settings.TKE_THRESHOLD_HIGH
    edr_high = edr_high if edr_high is not None else settings.EDR_THRESHOLD_HIGH

    level = LEVEL_LOW

    # 不可飞（硬约束）
    if _ge(tke, tke_high) or _ge(edr, edr_high):
        return LEVEL_UNFLYABLE

    # 高风险
    if _ge(tke, settings.TKE_RISK_HIGH) or _ge(edr, settings.EDR_RISK_HIGH):
        level = LEVEL_HIGH

    # 中度（不覆盖已有更高等级）
    if level == LEVEL_LOW and (_ge(tke, settings.TKE_RISK_MODERATE) or _ge(edr, settings.EDR_RISK_MODERATE)):
        level = LEVEL_MODERATE

    return level


def _ge(value: float, threshold: float) -> bool:
    """NaN 安全的大于等于比较：NaN 视为不满足。"""
    if value is None:
        return False
    try:
        if math.isnan(value):
            return False
    except TypeError:
        return False
    return value >= threshold


def _index(level: str) -> int:
    try:
        return LEVEL_ORDER.index(level)
    except ValueError:
        return 0


@dataclass
class RiskStats:
    """沿航线的风险统计指标。"""
    max_tke: float = 0.0
    max_edr: float = 0.0
    mean_tke: float = 0.0
    mean_edr: float = 0.0
    moderate_ratio: float = 0.0     # 中度及以上样本占比
    high_ratio: float = 0.0         # 高风险及以上样本占比
    max_exposure_km: float = 0.0    # 连续中度以上暴露的最长距离
    n_samples: int = 0
    worst_index: int = 0            # 风险最高样本的下标（便于前端定位）


@dataclass
class RiskSegment:
    """按城市间隔点切分的一段航线风险。"""
    from_name: str
    to_name: str
    level: str
    max_tke: float
    max_edr: float
    distance_km: float
    start_index: int
    end_index: int


@dataclass
class RiskProfile:
    """整条航线的风险剖面。"""
    overall: str
    stats: RiskStats
    samples: List[Dict[str, Any]] = field(default_factory=list)   # [{'i','lat','lon','tke','edr','level'}]
    segments: List[RiskSegment] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall": self.overall,
            "stats": asdict(self.stats),
            "samples": self.samples,
            "segments": [asdict(s) for s in self.segments],
        }


def build_risk_profile(samples: List[Dict[str, Any]],
                       segment_bounds: Optional[List[Dict[str, Any]]] = None,
                       tke_high: Optional[float] = None,
                       edr_high: Optional[float] = None) -> RiskProfile:
    """根据沿程采样点构建风险剖面。

    参数
    ----
    samples:
        每个元素形如 {'lat','lon','tke','edr','cum_km'(可选)}，
        cum_km 为自起点累计距离（km），用于计算暴露距离。
    segment_bounds:
        可选的分段边界，元素形如 {'from_name','to_name','start_index','end_index'}，
        用于输出「区域风险等级」（以重要城市为间隔点）。
    """
    n = len(samples)
    if n == 0:
        return RiskProfile(overall=LEVEL_LOW, stats=RiskStats(), samples=[], segments=[])

    tkes = [float(s.get('tke', 0.0) or 0.0) for s in samples]
    edrs = [float(s.get('edr', 0.0) or 0.0) for s in samples]
    levels = [classify_point(t, e, tke_high, edr_high) for t, e in zip(tkes, edrs)]

    max_tke = max(tkes)
    max_edr = max(edrs)
    mean_tke = sum(tkes) / n
    mean_edr = sum(edrs) / n
    moderate_idx = max(_index(LEVEL_MODERATE), 1)
    high_idx = _index(LEVEL_HIGH)
    moderate_count = sum(1 for lv in levels if _index(lv) >= moderate_idx)
    high_count = sum(1 for lv in levels if _index(lv) >= high_idx)

    # 连续暴露最长距离（中度及以上视为暴露）
    max_exposure = 0.0
    run_start = None
    for i, lv in enumerate(levels):
        exposed = _index(lv) >= moderate_idx
        if exposed and run_start is None:
            run_start = i
        elif not exposed and run_start is not None:
            max_exposure = max(max_exposure, _span_km(samples, run_start, i - 1))
            run_start = None
    if run_start is not None:
        max_exposure = max(max_exposure, _span_km(samples, run_start, n - 1))

    worst_index = max(range(n), key=lambda i: (_index(levels[i]), tkes[i], edrs[i]))

    stats = RiskStats(
        max_tke=round(max_tke, 4),
        max_edr=round(max_edr, 4),
        mean_tke=round(mean_tke, 4),
        mean_edr=round(mean_edr, 4),
        moderate_ratio=round(moderate_count / n, 4),
        high_ratio=round(high_count / n, 4),
        max_exposure_km=round(max_exposure, 2),
        n_samples=n,
        worst_index=int(worst_index),
    )

    overall = synthesize_overall(levels, stats)

    sample_out = []
    for i, s in enumerate(samples):
        sample_out.append({
            "i": i,
            "lat": s.get('lat'),
            "lon": s.get('lon'),
            "tke": round(tkes[i], 4),
            "edr": round(edrs[i], 4),
            "level": levels[i],
        })

    segments: List[RiskSegment] = []
    if segment_bounds:
        for sb in segment_bounds:
            a = int(sb.get('start_index', 0))
            b = int(sb.get('end_index', n - 1))
            a = max(0, min(a, n - 1))
            b = max(a, min(b, n - 1))
            seg_levels = levels[a:b + 1]
            seg_tkes = tkes[a:b + 1]
            seg_edrs = edrs[a:b + 1]
            seg_level = _synth_level(seg_levels, seg_tkes, seg_edrs)
            segments.append(RiskSegment(
                from_name=sb.get('from_name', ''),
                to_name=sb.get('to_name', ''),
                level=seg_level,
                max_tke=round(max(seg_tkes), 4),
                max_edr=round(max(seg_edrs), 4),
                distance_km=round(_span_km(samples, a, b), 2),
                start_index=a,
                end_index=b,
            ))

    return RiskProfile(overall=overall, stats=stats, samples=sample_out, segments=segments)


def synthesize_overall(levels: List[str], stats: RiskStats) -> str:
    """由沿程等级序列与统计量合成「全程风险等级」。"""
    if any(lv == LEVEL_UNFLYABLE for lv in levels):
        return LEVEL_UNFLYABLE

    base = LEVEL_LOW
    if any(lv == LEVEL_HIGH for lv in levels):
        base = LEVEL_HIGH
    elif any(lv == LEVEL_MODERATE for lv in levels):
        base = LEVEL_MODERATE

    # 中度以上占比过高时，整体等级上调一级
    if stats.moderate_ratio >= settings.RISK_MODERATE_RATIO_PROMOTE and _index(base) < _index(LEVEL_HIGH):
        base = LEVEL_HIGH if base == LEVEL_MODERATE else LEVEL_MODERATE

    return base


def _synth_level(levels: List[str], tkes: List[float], edrs: List[float]) -> str:
    if not levels:
        return LEVEL_LOW
    if any(lv == LEVEL_UNFLYABLE for lv in levels):
        return LEVEL_UNFLYABLE
    if any(lv == LEVEL_HIGH for lv in levels):
        return LEVEL_HIGH
    if any(lv == LEVEL_MODERATE for lv in levels):
        return LEVEL_MODERATE
    return LEVEL_LOW


def _span_km(samples: List[Dict[str, Any]], a: int, b: int) -> float:
    """返回 samples[a..b] 区间内的累计距离跨度（km）。

    优先使用样本自带的 cum_km；缺失时用经纬度近似累加。
    """
    if a >= b or a < 0 or b >= len(samples):
        return 0.0

    if all('cum_km' in samples[i] for i in range(a, b + 1)):
        return abs(float(samples[b]['cum_km']) - float(samples[a]['cum_km']))

    dist = 0.0
    for i in range(a + 1, b + 1):
        dist += _quick_km(samples[i-1], samples[i])
    return dist


def _quick_km(p: Dict[str, Any], q: Dict[str, Any]) -> float:
    lat_mid = math.radians((float(p['lat']) + float(q['lat'])) / 2.0)
    dx = (float(q['lon']) - float(p['lon'])) * math.cos(lat_mid)
    dy = float(q['lat']) - float(p['lat'])
    return math.hypot(dx, dy) * 111.0