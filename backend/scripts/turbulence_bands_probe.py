"""申报书公式 k 的抽样全年分布（500 hPa，±6 h 滑动窗口脉动方差），用来人工界定风险四档。

k = ½·( mean(u'²) + mean(v'²) + mean(w'²) )，u' = u_i − ū（窗口内平均）。
本脚本不设任何结论性阈值：只输出量级、按月/按时分布、与旧刻度的交叉验证。
阈值定完后的复核脚本见 turbulence_bands_report.py。

用法（要读 600 GB 数据盘，全跑约 25 分钟）：
    cd backend
    .python/portable/python.exe scripts/turbulence_bands_probe.py > probe.out

样本缓存写到仓库根的 .tmp-turb-bands/（已 gitignore），复核脚本从那里读。
"""
import os
import sys
import time
import warnings

import numpy as np
import xarray as xr

warnings.filterwarnings("ignore")
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)
from config import settings  # noqa: E402
from meteorology import pulsation_tke  # noqa: E402

# 与复核脚本共用，别改名字
CACHE_DIR = os.path.join(os.path.dirname(BACKEND_DIR), ".tmp-turb-bands")
os.makedirs(CACHE_DIR, exist_ok=True)

DATA = settings.DATA_DIR
FILES = {"u": settings.U_FILE, "v": settings.V_FILE, "w": settings.W_FILE}
BOXES = {
    # 拉萨—林芝走廊（当前用例主战场）
    "corridor": dict(lat=slice(29.0, 31.0), lon=slice(90.0, 95.0)),
    # 昌都窄谷（现状封路比例最高的区域）
    "qamdo": dict(lat=slice(30.5, 32.5), lon=slice(96.0, 98.0)),
    # 横断山东缘低海拔河谷（对流量级差异最大的对照区）
    "hengduan": dict(lat=slice(26.5, 28.5), lon=slice(97.5, 99.5)),
}
LEV = 500.0
HALF = 6          # 滑动半窗：±6h -> N=13
PRE = HALF        # 每天多读前后各 HALF 小时，保证首尾小时的窗口完整
# 每月 6 个样本日；只想快速验证脚本时用它缩小范围：TB_DAYS=1,15
MONTH_DAYS = [int(x) for x in os.environ.get("TB_DAYS", "1,5,10,15,20,25").split(",")]
CUTS = [4, 6, 8, 10, 12, 15, 18, 22, 26, 30, 35, 40, 50]
PCTS = [10, 25, 50, 75, 90, 95, 98, 99, 99.9, 100]

import pandas as pd  # noqa: E402


def read_window(ds, var, t0, n, lev_idx, box):
    da = ds[var].isel(lev=lev_idx).sel(**box)
    return da.isel(time=slice(t0, t0 + n)).values.astype(np.float64)


def sliding_var_whole_day(a):
    """对照用：整天均值的方差（生产代码不走这条，见 meteorology.pulsation_tke）。

    整天窗口下每个小时的方差是同一个值，广播回 (hours, ...) 才能和 ±6h 那条逐元素对比。
    """
    m = np.nanmean(a, axis=0, keepdims=True)
    with np.errstate(invalid="ignore"):
        var = np.nanmean((a - m) ** 2, axis=0)
    return np.broadcast_to(var, a.shape)


def k_whole_day(u, v, w):
    return 0.5 * (sliding_var_whole_day(u) + sliding_var_whole_day(v)
                  + sliding_var_whole_day(w))


def edr(k, ell, c_eps=0.8):
    with np.errstate(invalid="ignore"):
        return (c_eps * np.power(k, 1.5) / ell) ** (1.0 / 3.0)


def collect(box_name, box):
    """返回按小时展开的一维样本：k、现状 TKE、平均风速、月份、北京时间小时。"""
    frames = {"k": [], "now": [], "spd": [], "month": [], "hour": [], "k3": [], "k24": [], "kraw": []}
    with xr.open_dataset(os.path.join(DATA, FILES["u"])) as du, \
         xr.open_dataset(os.path.join(DATA, FILES["v"])) as dv, \
         xr.open_dataset(os.path.join(DATA, FILES["w"])) as dw:
        lev_idx = int(np.abs(np.asarray(du.lev.values, float) - LEV).argmin())
        assert float(du.lev.values[lev_idx]) == LEV
        n_time = du.sizes["time"]
        for m in range(1, 13):
            for d in MONTH_DAYS:
                day = pd.Timestamp(2022, m, d)
                t0 = (day - pd.Timestamp(2022, 1, 1)).days * 24 - PRE   # 前推半窗（UTC 标签）
                t0 = max(0, t0)
                n = 24 + 2 * PRE
                if t0 + n > n_time:
                    continue
                u = read_window(du, "u", t0, n, lev_idx, box)
                v = read_window(dv, "v", t0, n, lev_idx, box)
                w = read_window(dw, "w", t0, n, lev_idx, box)
                # 走生产代码的同一个入口，量的就是系统真正用的那个 k
                kk = pulsation_tke(u, v, w, half_hours=HALF)[PRE:PRE + 24]
                k3 = pulsation_tke(u, v, w, half_hours=3)[PRE:PRE + 24]
                k24 = k_whole_day(u[PRE:PRE + 24], v[PRE:PRE + 24], w[PRE:PRE + 24])
                now = 0.01 * 0.5 * (u[PRE:PRE + 24] ** 2 + v[PRE:PRE + 24] ** 2
                                    + w[PRE:PRE + 24] ** 2)
                spd = np.sqrt(u[PRE:PRE + 24] ** 2 + v[PRE:PRE + 24] ** 2
                               + w[PRE:PRE + 24] ** 2)
                # 对照：不做 1/N 平均的逐小时瞬时脉动
                pu = u - np.nanmean(u, axis=0, keepdims=True)
                pv = v - np.nanmean(v, axis=0, keepdims=True)
                pw = w - np.nanmean(w, axis=0, keepdims=True)
                kraw = (0.5 * (pu ** 2 + pv ** 2 + pw ** 2))[PRE:PRE + 24]
                # 各数组同形状，用联合掩码保持逐元素对齐后再展平
                mask = np.isfinite(kk) & np.isfinite(now) & np.isfinite(spd)
                hh = np.broadcast_to(np.arange(24)[:, None, None], kk.shape)
                bj = (hh + 8) % 24          # 数据标签是 UTC，换算北京时间
                for key, arr in (("k", kk), ("now", now), ("spd", spd), ("k3", k3),
                                 ("k24", k24), ("kraw", kraw), ("hour", bj)):
                    frames[key].append(arr[mask])
                frames["month"].append(np.full(int(mask.sum()), m))
    return {k: np.concatenate(v) for k, v in frames.items()}


def line(tag, a):
    a = a[np.isfinite(a)]
    if a.size == 0:
        print("  %-26s 无样本" % tag)
        return
    p = np.percentile(a, PCTS)
    print("  %-26s n=%-8d " % (tag, a.size)
          + " ".join("p%g %7.2f" % (q, x) for q, x in zip(PCTS, p)))


for name, box in BOXES.items():
    t_start = time.time()
    S = collect(name, box)
    n = S["k"].size
    print("\n########  %s  500hPa  样本小时格点 %d  (%.0fs)  ########" % (name, n, time.time() - t_start))
    line("k  滑动 ±6h (N=13)", S["k"])
    line("k3 滑动 ±3h (N=7)", S["k3"])
    line("k24 整天 (N=24)", S["k24"])
    line("对照 瞬时 0.5|u'|^2", S["kraw"])
    line("现状刻度 0.005|U|^2", S["now"])
    print("  平均风 |U|  m/s:  " + " ".join("p%g %.2f" % (q, x) for q, x in
          zip(PCTS, np.percentile(S["spd"], PCTS))))

    print("  --- 候选硬阈值：命中占比 / 该占比下的 EDR(l=2000) / 隐含脉动幅度 ---")
    for c in CUTS:
        hit = (S["k"] >= c).mean() * 100
        sig = np.sqrt(2.0 * c / 3.0)                    # 各向同性下 sigma = sqrt(2k/3)
        e = float(edr(np.array([c]), 2000.0)[0])
        e1 = float(edr(np.array([c]), 1000.0)[0])
        print("    k>=%-4g  占比 %6.3f%%   sigma>=%4.2f m/s   EDR>=%.3f(l=2000) / %.3f(l=1000)"
              % (c, hit, sig, e, e1))

    print("  --- 现状 TKE>=1.0 的封路占比 = %.3f%% ---" % ((S["now"] >= 1.0).mean() * 100))
    ref = (S["now"] >= 1.0)
    for c in (6, 8, 10, 12, 15, 22):
        new = (S["k"] >= c)
        both = int((ref & new).sum())
        print("    k>=%-3g: 新判危险 %.3f%% | 与现状交集占现状 %.1f%% 占新 %.1f%%"
              % (c, new.mean() * 100,
                 100.0 * both / max(1, int(ref.sum())),
                 100.0 * both / max(1, int(new.sum()))))
    # 秩相关与「现状最危险的前 q%」在新刻度里还剩多少
    from scipy.stats import spearmanr
    print("    Spearman(现状, k) = %.3f" % spearmanr(S["now"], S["k"]).statistic)
    for q in (0.02, 0.05):
        m1 = int(S["now"].size * q)
        a = set(np.argsort(-S["now"])[:m1].tolist())
        b = set(np.argsort(-S["k"])[:m1].tolist())
        print("    现状最危险前 %.0f%% 格点中，仍在新刻度最危险前 %.0f%% 内的比例 = %.1f%%"
              % (q * 100, q * 100, 100.0 * len(a & b) / max(1, len(a))))

    print("  --- 按北京时间小时的 k 中位数（日变化信号是否保留）---")
    prof = [(h, float(np.median(S["k"][S["hour"] == h])),
             float(np.percentile(S["k"][S["hour"] == h], 95))) for h in range(24)]
    print("    中位 " + " ".join("%.1f" % v for _, v, _ in prof))
    print("    p95  " + " ".join("%.1f" % v for _, _, v in prof))
    med = [v for _, v, _ in prof]
    print("    日内峰谷比 %.2f×（最高 %.1f @ %02d 时，最低 %.1f @ %02d 时）"
          % (max(med) / max(1e-9, min(med)), max(med), int(np.argmax(med)),
             min(med), int(np.argmin(med))))

    print("  --- 按月：k 分位数与候选占比 ---")
    for m in range(1, 13):
        sel = S["month"] == m
        k = S["k"][sel]
        spd = S["spd"][sel]
        now = S["now"][sel]
        p = np.percentile(k, [50, 90, 99, 100])
        ti = np.sqrt(2 * k / 3.0) / np.maximum(spd, 0.5)   # 湍流强度近似
        print("    %02d月 p50 %6.2f p90 %6.2f p99 %6.2f max %7.2f | |U|p50 %5.1f | "
              "TI p50 %.2f p90 %.2f | 现状>=1.0 %5.2f%% | k>=12 %5.2f%% k>=26 %5.2f%%"
              % (m, p[0], p[1], p[2], p[3], np.median(spd),
                 np.median(ti), np.percentile(ti, 90),
                 (now >= 1.0).mean() * 100, (k >= 12).mean() * 100, (k >= 26).mean() * 100))
    np.save(os.path.join(CACHE_DIR, "kdist_%s.npy" % name),
            np.vstack([S["k"], S["now"], S["spd"], S["month"], S["hour"],
                       S["k3"], S["k24"], S["kraw"]]))
