"""
风险分级与航线风险统计。

阈值口径与 planner.py 的软/硬约束保持一致（TKE 取脉动方差刻度）：
  - 安全（低风险）：  TKE < 8    且 EDR < 0.208
  - 中风险：        TKE >= 8    或 EDR >= 0.208
  - 高风险：        TKE >= 16   或 EDR >= 0.295
  - 完全没法飞（硬约束）：TKE >= 26 或 EDR >= 0.376
"""
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
import math

from config import settings
from geo import KM_PER_DEG

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


def level_rank(level: str, default: int = 0) -> int:
    """等级在 LEVEL_ORDER 里的序位（0=低风险 … 3=不可飞）；认不出的等级返回 default。

    default 往哪边兜由调用点自己定，三个模块的口径本来就不同，不要在这里替它们统一：
      - risk 统计用 0：样本在上游已判过合法性，认不出就当低风险；
      - sites 复验用 -1：认不出就排在所有已知等级之前，即不当成好消息；
      - time_window 筛选用 3：认不出就当不可飞，宁可严。
    """
    try:
        return LEVEL_ORDER.index(level)
    except ValueError:
        return default


def _worst_level(levels: List[str]) -> str:
    """沿程等级序列里最差的一档。只认 LEVEL_ORDER 里的四档，其余按低风险兜。"""
    return LEVEL_ORDER[max((level_rank(lv) for lv in levels), default=0)]


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


def _sample_num(value: Any) -> float:
    """把样本上的一个量取成 float；缺测（None / NaN / 非数 / Inf）一律返回 NaN。

    **不能**写成 ``float(value or 0.0)``：NaN 是 falsy，那个惯用法会把
    「没测到」静默变成 0.0，而 0.0 恰好是最安全的湍流值 —— 于是整条航线
    都没测到时，统计量会显示成「全程零湍流」。这里让缺测一路保持 NaN。
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return float('nan')
    return v if math.isfinite(v) else float('nan')


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
        # 空样本没有任何依据。给 LEVEL_LOW 是在说「查过了，风平浪静」，
        # 而实际是「一个点都没测到」。这里仍返回 LEVEL_LOW（等级枚举里没有
        # 「未判定」这一档，强行加会波及前端配色与文案），但把 max_tke/max_edr
        # 置成 NaN，让「未知」这件事在数值上看得见 —— 上层按 isfinite 判。
        return RiskProfile(overall=LEVEL_LOW,
                           stats=RiskStats(max_tke=float('nan'), max_edr=float('nan')),
                           samples=[], segments=[])

    tkes = [_sample_num(s.get('tke')) for s in samples]
    edrs = [_sample_num(s.get('edr')) for s in samples]
    levels = [classify_point(t, e, tke_high, edr_high) for t, e in zip(tkes, edrs)]

    # 统计量只在**有限样本**上算。一个有限样本都没有时给 NaN 而不是 0.0 ——
    # 0.0 是最安全的湍流值，拿它当「没测到」的统计结果，等于凭空给出一份安全证明。
    # NaN 经 pydantic 序列化成 JSON null，前端画成断点而不是一条贴地的零线。
    fin_tke = [t for t in tkes if not math.isnan(t)]
    fin_edr = [e for e in edrs if not math.isnan(e)]
    max_tke = max(fin_tke) if fin_tke else float('nan')
    max_edr = max(fin_edr) if fin_edr else float('nan')
    mean_tke = (sum(fin_tke) / len(fin_tke)) if fin_tke else float('nan')
    mean_edr = (sum(fin_edr) / len(fin_edr)) if fin_edr else float('nan')
    moderate_idx = max(level_rank(LEVEL_MODERATE), 1)
    high_idx = level_rank(LEVEL_HIGH)
    moderate_count = sum(1 for lv in levels if level_rank(lv) >= moderate_idx)
    high_count = sum(1 for lv in levels if level_rank(lv) >= high_idx)

    # 连续暴露最长距离（中度及以上视为暴露）
    max_exposure = 0.0
    run_start = None
    for i, lv in enumerate(levels):
        exposed = level_rank(lv) >= moderate_idx
        if exposed and run_start is None:
            run_start = i
        elif not exposed and run_start is not None:
            max_exposure = max(max_exposure, _span_km(samples, run_start, i - 1))
            run_start = None
    if run_start is not None:
        max_exposure = max(max_exposure, _span_km(samples, run_start, n - 1))

    # 排序键里的 NaN 换成 -inf：直接用 NaN 参与元组比较，结果取决于比较顺序，
    # 「最危险的那个点」会变得不可复现。缺测的点本来也不该被选成最危险。
    worst_index = max(range(n), key=lambda i: (
        level_rank(levels[i]),
        tkes[i] if not math.isnan(tkes[i]) else float('-inf'),
        edrs[i] if not math.isnan(edrs[i]) else float('-inf')))

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
            # 距起点里程：地图标「沿程最危险点」时要说清它在航线的哪儿
            "cum_km": s.get('cum_km'),
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
            seg_level = _worst_level(seg_levels)
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
    base = _worst_level(levels)

    # 中度以上占比过高时，整体等级上调一级（已是「不可飞」就不再上调，那已是顶格）
    if stats.moderate_ratio >= settings.RISK_MODERATE_RATIO_PROMOTE and level_rank(base) < level_rank(LEVEL_HIGH):
        base = LEVEL_HIGH if base == LEVEL_MODERATE else LEVEL_MODERATE

    return base


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
    return math.hypot(dx, dy) * KM_PER_DEG