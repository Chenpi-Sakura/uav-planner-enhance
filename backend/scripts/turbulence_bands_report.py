"""风险四档界定的复核表：读取 turbulence_bands_probe.py 的样本缓存，重算所有引用到的数字。

改阈值 / 改窗口 / 改 EDR 换算时先跑这个，别改 `backend/config.py` 的注释。
缓存不存在时先跑 probe（要扫数据盘，约 25 分钟）。

用法：
    cd backend
    .python/portable/python.exe scripts/turbulence_bands_report.py > report.out
"""
import os
import sys

import numpy as np

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)
from config import settings  # noqa: E402

CACHE_DIR = os.path.join(os.path.dirname(BACKEND_DIR), ".tmp-turb-bands")
BOXES = ("corridor", "qamdo", "hengduan")

# 8 行布局带「北京时间小时」；7 行是早期缓存，没有这一行。
COLS = {8: dict(k=0, now=1, spd=2, month=3, hour=4, k3=5, k24=6, kraw=7),
        7: dict(k=0, now=1, spd=2, month=3, hour=None, k3=4, k24=5, kraw=6)}

CUTS = [settings.TKE_RISK_MODERATE, settings.TKE_RISK_HIGH, settings.TKE_THRESHOLD_HIGH]
BANDS = ["安全", "中风险", "高风险", "完全没法飞"]
PS = (50, 75, 90, 95, 98, 99, 99.9)


def edr(k):
    """ε = C·k^1.5 / L，EDR = ε^(1/3)；与 meteorology.edr_from_tke 同式。"""
    return (settings.TURB_EDR_C_EPS * np.asarray(k, float) ** 1.5
            / settings.TURB_EDR_LENGTH_SCALE_M) ** (1.0 / 3.0)


def rank(a):
    order = np.argsort(a, kind="stable")
    r = np.empty(a.shape, dtype=float)
    r[order] = np.arange(a.size)
    return r


def load(box):
    path = os.path.join(CACHE_DIR, "kdist_%s.npy" % box)
    if not os.path.exists(path):
        raise SystemExit("缺样本缓存 %s —— 先跑 scripts/turbulence_bands_probe.py" % path)
    a = np.load(path)
    return a, COLS[a.shape[0]]


D = {b: load(b) for b in BOXES}

print("样本量（格点·小时）：", {b: int(D[b][0].shape[1]) for b in BOXES})

print("\n=== k 分位数 [m²/s²]（左对齐 EDR 取走廊区）===")
print("%-8s %-9s" % ("cut", "EDR") + "".join("%12s" % b for b in BOXES))
for p in PS:
    print("%-8s %-13.3f" % ("p%g" % p, edr(np.percentile(D["corridor"][0][0], p)))
          + "".join("%12.2f" % np.percentile(D[b][0][COLS[D[b][0].shape[0]]["k"]], p)
                    for b in BOXES))
print("%-8s %-13.3f" % ("max", edr(D["corridor"][0][0].max()))
      + "".join("%12.2f" % D[b][0][COLS[D[b][0].shape[0]]["k"]].max() for b in BOXES))

print("\n=== 四档占用（%，按 config.py 的当前切点）===")
edges = [0.0] + CUTS + [np.inf]
print("%-24s" % "档" + "".join("%12s" % b for b in BOXES))
for i, name in enumerate(BANDS):
    lo, hi = edges[i], edges[i + 1]
    row = "%-24s" % ("%s [%g,%s)" % (name, lo, "%g" % hi if hi != np.inf else "∞"))
    for b in BOXES:
        a, c = D[b]
        k = a[c["k"]]
        row += "%11.2f%%" % (100.0 * ((k >= lo) & (k < hi)).mean())
    print(row)

print("\n=== 切点的物理量级（σ = √(2k/3)，各向同性下的脉动标准差）===")
for c in CUTS:
    print("  k=%-6g EDR=%.3f  σ=%.2f m/s" % (c, edr(c), np.sqrt(2 * c / 3.0)))

print("\n=== 逐月「高风险及以上」与「不可飞」占比（%）===")
for b in BOXES:
    a, c = D[b]
    k, mm = a[c["k"]], a[c["month"]].astype(int)
    print("  %-10s >=%g: %s" % (b, CUTS[1],
          " ".join("%5.1f" % (100.0 * (k[mm == m] >= CUTS[1]).mean()) for m in range(1, 13))))
    print("  %-10s >=%g: %s" % ("", CUTS[2],
          " ".join("%5.1f" % (100.0 * (k[mm == m] >= CUTS[2]).mean()) for m in range(1, 13))))

if D["corridor"][0].shape[0] == 8:
    print("\n=== 日变化（北京时）：日内峰谷比 ===")
    for b in BOXES:
        a, c = D[b]
        k, hh = a[c["k"]], a[c["hour"]].astype(int)
        prof = np.array([np.median(k[hh == h]) for h in range(24)])
        print("  %-10s 峰 %.2f @%02d 时  谷 %.2f @%02d 时  %.2f×"
              % (b, prof.max(), int(prof.argmax()), prof.min(), int(prof.argmin()),
                 prof.max() / prof.min()))

print("\n=== 与旧刻度（0.005·|U|²，即把平均风当湍流）的分歧 ===")
for b in BOXES:
    a, c = D[b]
    k, old = a[c["k"]], a[c["now"]]
    rho = float(np.corrcoef(rank(old), rank(k))[0, 1])
    n = max(int(old.size * 0.02), 1)
    top_old = set(np.argsort(-old, kind="stable")[:n].tolist())
    top_new = set(np.argsort(-k, kind="stable")[:n].tolist())
    print("  %-10s Spearman=%.3f  旧 top2%% 仍在新 top2%% 的占 %.1f%%  旧 TKE>=1.0 占 %.2f%%"
          % (b, rho, 100.0 * len(top_old & top_new) / n, 100.0 * (old >= 1.0).mean()))
    print("             旧 p50 %.3f / p99 %.3f  ←→  新 p50 %.2f / p99 %.2f"
          % (np.median(old), np.percentile(old, 99), np.median(k), np.percentile(k, 99)))

print("\n=== 平均风速 |U| m/s（旧口径的唯一输入）===")
for b in BOXES:
    a, c = D[b]
    spd = a[c["spd"]]
    print("  %-10s p50 %.1f  p99 %.1f  max %.1f" % (b, np.median(spd),
                                                    np.percentile(spd, 99), spd.max()))

print("\n=== 窗口半径敏感性 ===")
for b in BOXES:
    a, c = D[b]
    for key, tag in (("k", "±%dh(采用)" % settings.TURB_WINDOW_HALF_HOURS),
                     ("k3", "±3h"), ("k24", "整天24h")):
        k = a[c[key]]
        print("  %-10s %-12s p50 %6.2f  p99 %6.2f  >=%g %5.2f%%"
              % (b, tag, np.median(k), np.percentile(k, 99), CUTS[2],
                 100.0 * (k >= CUTS[2]).mean()))

print("\n=== 扣除方式对照：1/N 平均 vs 逐小时瞬时脉动 ===")
for b in BOXES:
    a, c = D[b]
    print("  %-10s 瞬时 p50 %.2f  p99 %.2f  max %.1f   vs  采用口径 p99 %.2f"
          % (b, np.median(a[c["kraw"]]), np.percentile(a[c["kraw"]], 99),
             a[c["kraw"]].max(), np.percentile(a[c["k"]], 99)))
