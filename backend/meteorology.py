r"""湍流量单入口：申报书口径的脉动方差 TKE 与由其推导的 EDR。

所有取数路径（A* 寻路、时间窗口扫描、沿程评估、出图、单点查询）都必须调用本模块，
不允许在各自模块内重写公式——否则地图与寻路会用到两套不同的湍流场，
结论与出图互相矛盾。

物理口径（照抄申报书符号，\overline{\cdot} 即上划线，表示窗口内平均）：
    u' = u − ū，\overline{u'^2} = (1/N) Σ_i (u_i − ū)²
    k = ½ · ( \overline{u'^2} + \overline{v'^2} + \overline{w'^2} )
    单位 m²/s²（≡ J/kg）
旧实现直接拿平均风的动能 0.005·|U|² 当湍流，把急流轴当成全域最强湍流区，
方向性错误；这里改为扣除窗口平均后的脉动方差。
"""
import numpy as np

from config import settings


def _sliding_var(a: np.ndarray, half: int, min_samples: int) -> np.ndarray:
    """沿第 0 轴（小时）做中心滑动窗口的总体方差，NaN 感知。

    用累积和实现，单次遍历即可，代价与窗口宽度无关。
    边缘处窗口被截断（不退化成单点），有效样本不足 min_samples 时给 NaN。
    """
    n = a.shape[0]
    z = np.where(np.isfinite(a), a, 0.0)
    c1 = np.cumsum(z, axis=0)
    c2 = np.cumsum(z * z, axis=0)
    cc = np.cumsum(np.isfinite(a).astype(np.int32), axis=0)

    def upto(c, i):
        return c[i] if i >= 0 else 0.0

    out = np.full(a.shape, np.nan)
    for i in range(n):
        hi = min(n - 1, i + half)
        lo = max(0, i - half)
        s1 = upto(c1, hi) - upto(c1, lo - 1)
        s2 = upto(c2, hi) - upto(c2, lo - 1)
        cnt = upto(cc, hi) - upto(cc, lo - 1)
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = s1 / cnt
            var = s2 / cnt - mean * mean
        # E[x²]−E[x]² 的舍入误差可能给出极小负值
        out[i] = np.where(cnt >= min_samples, np.maximum(var, 0.0), np.nan)
    return out


def pulsation_tke(u, v, w, half_hours: int = None, min_samples: int = None) -> np.ndarray:
    """按申报书公式计算脉动方差 TKE。

    u/v/w 为同一层、同一格点集的风场，形状 (hours, ...)，单位 m/s。
    返回与输入同形状的 k [m²/s²]。

    目标小时自身缺测时结果为 NaN：下游把 NaN 当作「地形遮挡/无数据」，
    窗口平均不能把这个语义冲掉，否则不可飞判据会退化成「到处可飞」。
    """
    half = int(round(float(settings.TURB_WINDOW_HALF_HOURS
                           if half_hours is None else half_hours)))
    min_cnt = int(round(float(settings.TURB_MIN_WINDOW_SAMPLES
                              if min_samples is None else min_samples)))
    comps = [np.asarray(c, dtype="float64") for c in (u, v, w)]
    if any(c.shape != comps[0].shape for c in comps):
        raise ValueError("u/v/w 形状必须一致（同层、同格点集）")
    if half < 1:
        raise ValueError("half_hours 至少为 1，否则没有脉动可言")

    k = 0.5 * sum(_sliding_var(c, half, min_cnt) for c in comps)
    valid = np.isfinite(comps[0]) & np.isfinite(comps[1]) & np.isfinite(comps[2])
    return np.where(valid, k, np.nan)


def edr_from_tke(tke, length_scale_m: float = None, c_eps: float = None):
    """由 TKE 推导 EDR（Eddy Dissipation Rate）。

    ε = C_ε · k^1.5 / L  [m²/s³]，EDR = ε^(1/3) [m^(2/3)/s]。
    L 取湍流外尺度（能量含藏尺度），不是飞行器尺度：本数据约 5 km 网格，
    只能分辨到公里级涡，L 取 2 km 量级。EDR 与 TKE 是同一个场的两种单位，
    因此两者的分档阈值必须互为换算，不能各标一套。
    """
    ell = float(settings.TURB_EDR_LENGTH_SCALE_M if length_scale_m is None else length_scale_m)
    c = float(settings.TURB_EDR_C_EPS if c_eps is None else c_eps)
    k = np.asarray(tke, dtype="float64")
    eps = c * np.power(np.clip(k, 0.0, None), 1.5) / ell
    return np.power(eps, 1.0 / 3.0)
