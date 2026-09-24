"""寻路结果的金标基线：用于验证「实现换了、结论没换」。

用法：
    cd backend
    .python/portable/python.exe tests/golden_plan.py <输出.json> [--synth-only]

设计约定（改前后必须逐字节可比）：
  ① 所有浮点用 repr() 输出，不用 round()，也不走 np.allclose 那种容差比较；
     等价性的声明是「逐比特相同」，允许误差就再也追不回边界翻转的证据。
  ② JSON 里只放结果，不放耗时；耗时打到 stdout，否则前后 diff 永远不为空。
  ③ 除真实航线外，必须包含「半小时整平局」的合成用例：时间轴最近邻查询在
     并列时取最小下标，这条语义最容易在换成 searchsorted 时被悄悄改掉。

--synth-only 跳过真实 NetCDF，只跑合成用例（CI 用，不碰数据盘）。
"""
import json
import os
import sys
import time

import numpy as np

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import meteorology  # noqa: E402
import planner  # noqa: E402
from planner import plan_path_ex, plan_path_segment  # noqa: E402
from schemas import PlanRequest  # noqa: E402
from config import settings  # noqa: E402

# 合成用例里的湍流量一律由 settings 推导：阈值换口径后写死的数字会失去含义。
TKE_MID = settings.TKE_RISK_MODERATE
EDR_MID = settings.EDR_RISK_MODERATE
TKE_UNFLYABLE = settings.TKE_THRESHOLD_HIGH
EDR_UNFLYABLE = settings.EDR_THRESHOLD_HIGH
TKE_BAD = TKE_UNFLYABLE * 1.5
EDR_BAD = EDR_UNFLYABLE * 1.5


# ==================== 真实数据用例 ====================
# 坐标取自 /api/meta/cities；覆盖：常规/长途/带航点/短程/阈值收紧/阈值放宽/
# 不同出发时刻（换时间轴起点）/不同巡航速度（改距离→时刻映射）。
# 阈值写成 settings 的倍数而不是字面量，否则湍流口径一重标定，这两条就悄悄
# 变成了「比默认更松」和「比默认更紧」的反面。
_TIGHT_TKE = settings.TKE_RISK_MODERATE * 1.25          # 略高于中风险下沿
_LOOSE_TKE = settings.TKE_THRESHOLD_HIGH * 2.5          # 硬阈值的 2.5 倍


def _edr_at(tke_value: float) -> float:
    """让 EDR 阈值与 TKE 阈值切在同一个物理点上（同一场的两种单位）。"""
    return float(meteorology.edr_from_tke(tke_value))


REAL_CASES = [
    ("r_lhasa_shigatse", dict(start={"lat": 29.65, "lon": 91.13}, end={"lat": 29.27, "lon": 88.88})),
    ("r_lhasa_nyingchi", dict(start={"lat": 29.65, "lon": 91.13}, end={"lat": 29.66, "lon": 94.36})),
    ("r_via_nym", dict(start={"lat": 29.65, "lon": 91.13},
                       waypoints=[{"lat": 29.43, "lon": 90.16}],
                       end={"lat": 29.27, "lon": 88.88})),
    ("r_dangxung_linzhou", dict(start={"lat": 30.48, "lon": 91.1}, end={"lat": 30.05, "lon": 91.26})),
    ("r_tight_thresh", dict(start={"lat": 29.65, "lon": 91.13}, end={"lat": 29.27, "lon": 88.88},
                            tke_threshold=_TIGHT_TKE, edr_threshold=_edr_at(_TIGHT_TKE))),
    ("r_loose_thresh", dict(start={"lat": 29.65, "lon": 91.13}, end={"lat": 29.27, "lon": 88.88},
                            tke_threshold=_LOOSE_TKE, edr_threshold=_edr_at(_LOOSE_TKE))),
    ("r_start_20h", dict(start={"lat": 29.65, "lon": 91.13}, end={"lat": 29.27, "lon": 88.88},
                         start_time="20:00")),
    ("r_speed45", dict(start={"lat": 29.65, "lon": 91.13}, end={"lat": 29.27, "lon": 88.88}, speed=45.0)),
    ("r_speed120", dict(start={"lat": 31.48, "lon": 92.05}, end={"lat": 31.21, "lon": 96.6}, speed=120.0)),
]


# ==================== 合成用例：平局与降级语义 ====================
class _StubLoader:
    """替身 data_loader：给一块可控网格，lat 轴故意降序。

    真实文件是升序（25.94 -> 40.30），这里反过来是为了让 `_GridIndex` 的两个
    方向都被覆盖到；holes 用于把「越限」做成只发生在某一个小时、某几个格点上，
    这样才能造出「早小时安全、晚小时危险」型平局（见 synth_cases 注释）。
    """

    def __init__(self, tke_by_hour, edr_by_hour, hours=25, n=21, res_deg=0.5, holes=None):
        self.tke_by_hour = list(tke_by_hour)
        self.edr_by_hour = list(edr_by_hour)
        self.hours = hours
        self.n = n
        self.holes = holes or {}
        # 降序 lat / 升序 lon：一降一升，两个方向的平局规则都要走对
        self.lats = np.linspace(30.0 + res_deg * (n // 2), 30.0 - res_deg * (n // 2), n)
        self.lons = np.linspace(90.0, 90.0 + res_deg * (n - 1), n)
        self.times = np.arange(hours) * np.timedelta64(1, 'h') + np.datetime64('2022-07-01T08:00')

    def get_meteorology_grid(self, min_lat, max_lat, min_lon, max_lon, date=None):
        tke = np.zeros((self.hours, self.n, self.n), dtype=float)
        edr = np.zeros((self.hours, self.n, self.n), dtype=float)
        for i in range(self.hours):
            tke[i, :, :] = self.tke_by_hour[i % len(self.tke_by_hour)]
            edr[i, :, :] = self.edr_by_hour[i % len(self.edr_by_hour)]
        for (h, lat, lon), bad in self.holes.items():
            i = int(np.abs(self.times - (self.times[0] + np.timedelta64(h, 'h'))).argmin())
            j = int(np.abs(self.lats - lat).argmin())
            k = int(np.abs(self.lons - lon).argmin())
            tke[i, j, k] = TKE_BAD if bad else 0.0
        return tke, edr, self.lats, self.lons, self.times


def _segment_result(seg):
    return {
        "found": bool(seg.found),
        "reason": seg.reason,
        "iterations": int(seg.iterations),
        "explored": int(seg.explored),
        "final_g_score": repr(float(seg.final_g_score)),
        "final_dist": repr(float(seg.final_dist)),
        "path": [[repr(p.lat), repr(p.lon)] for p in seg.path],
        "times": [str(t) for t in seg.times],
        "tkes": [repr(float(x)) for x in seg.tkes],
        "edrs": [repr(float(x)) for x in seg.edrs],
    }


def synth_cases():
    """返回 (名称, 可调用) 列表；调用后得到可序列化的结果字典。

    平局是这么造出来的：resolution 置 0.5°、走纯纬度跳，一跳物理距离
    = 0.5*111.0 = 55.5 km；speed=111.0 km/h 时 elapsed 恰为 0.5 h，
    int(0.5*3600) = 1800 秒，正落在两个整点中间 -> 时间轴最近邻并列。
    起点自身 elapsed=0，采样到的是 hour0，所以洞必须打在具体格点上
    （holes），不能整小时置值，否则起点判定就把平局场景挡在前面。

    取 hour0（下标小者）与取 hour1 会给出不同的 found/路径，这两条用例
    一正一反，专门用来抓 searchsorted(side='right') 之类的改写。
    """
    out = []
    res_saved = planner.settings.GRID_RESOLUTION_DEG

    def _run(loader, start, end, speed, tke=TKE_UNFLYABLE, edr=EDR_UNFLYABLE):
        planner.data_loader = loader
        planner.settings.GRID_RESOLUTION_DEG = 0.5
        seg = plan_path_segment(start, end, tke, edr, '2022-07-01T08:00', 0.0, 0.0, speed)
        return _segment_result(seg)

    def tie_earlier_safe():
        """平局点：hour0 安全、hour1 有洞。正确实现应取 hour0 并继续通行。"""
        return _run(_StubLoader([0.0], [0.0], holes={(1, 29.5, 90.0): True}),
                    (30.0, 90.0), (27.5, 90.0), 111.0)

    def tie_earlier_bad():
        """反向：hour0 有洞、hour1 安全。正确实现取 hour0 -> 该邻居被拒，须绕行。"""
        return _run(_StubLoader([0.0], [0.0], holes={(0, 29.5, 90.0): True}),
                    (30.0, 90.0), (27.5, 90.0), 111.0)

    def exact_hour():
        """elapsed 恰好压在整点上（无平局），锁定另一侧边界。"""
        return _run(_StubLoader([0.0, TKE_BAD], [0.0]), (30.0, 90.0), (27.5, 90.0), 222.0)

    def start_blocked():
        return _run(_StubLoader([TKE_BAD], [EDR_BAD]), (30.0, 90.0), (29.0, 91.0), 60.0)

    def all_nan():
        """数据缺失区：NaN 一律按不安全处理。"""
        loader = _StubLoader([0.0], [0.0])
        grid = np.full((loader.hours, loader.n, loader.n), np.nan)
        loader.get_meteorology_grid = lambda *a, **k: (
            grid.copy(), grid.copy(), loader.lats, loader.lons, loader.times)
        return _run(loader, (30.0, 90.0), (29.0, 91.0), 60.0)

    def soft_penalty_road():
        """全程可飞但普遍处于中度湍流，锁定软惩罚的代价累加顺序。"""
        return _run(_StubLoader([TKE_MID * 1.2], [EDR_MID * 1.2]),
                    (30.0, 90.0), (28.0, 92.0), 60.0)

    out.append(("s_tie_earlier_safe", tie_earlier_safe))
    out.append(("s_tie_earlier_bad", tie_earlier_bad))
    out.append(("s_exact_hour", exact_hour))
    out.append(("s_start_blocked", start_blocked))
    out.append(("s_all_nan", all_nan))
    out.append(("s_soft_penalty_road", soft_penalty_road))
    return out, res_saved


# ==================== 序列化 ====================
def canon(res):
    return {
        "found": bool(res.found),
        "reason": res.reason,
        "total_distance": repr(float(res.total_distance)),
        "total_grid_cost": repr(float(res.total_grid_cost)),
        "waypoints_reached": list(res.waypoints_reached),
        "path": [[repr(p.lat), repr(p.lon)] for p in res.path],
        "times": [str(t) for t in res.times],
        "tkes": [repr(float(x)) for x in res.tkes],
        "edrs": [repr(float(x)) for x in res.edrs],
        "segments": [_segment_result(s) for s in res.segment_results],
    }


def main():
    args = [a for a in sys.argv[1:]]
    synth_only = "--synth-only" in args
    out_path = next((a for a in args if not a.startswith("--")), None)
    if out_path is None:
        print("用法: python tests/golden_plan.py <输出.json> [--synth-only]", file=sys.stderr)
        return 2

    # 用计时包装包住取数，才能把「A* 搜索」和「网格读取」分开量化
    from data_loader import data_loader
    real_fetch = data_loader.get_meteorology_grid
    stat = {"sec": 0.0, "n": 0}

    def timed(*a, **k):
        t0 = time.perf_counter()
        r = real_fetch(*a, **k)
        stat["sec"] += time.perf_counter() - t0
        stat["n"] += 1
        return r

    data_loader.get_meteorology_grid = timed
    planner.data_loader.get_meteorology_grid = timed

    payload = {"cases": {}}
    order = []

    if not synth_only:
        for name, kw in REAL_CASES:
            req = PlanRequest(**kw)
            t0 = time.perf_counter()
            f0 = stat["sec"]
            res = plan_path_ex(req)
            dt = time.perf_counter() - t0
            fetch = stat["sec"] - f0
            payload["cases"][name] = canon(res)
            order.append((name, dt, fetch))
            print("%-22s total %6.3fs  fetch %6.3fs  search %6.3fs  nodes %6d  found=%s %s"
                  % (name, dt, fetch, dt - fetch, len(res.path), res.found, res.reason or ""),
                  file=sys.stderr)

    cases, res_saved = synth_cases()
    try:
        for name, fn in cases:
            t0 = time.perf_counter()
            payload["cases"][name] = fn()
            dt = time.perf_counter() - t0
            order.append((name, dt, 0.0))
            print("%-22s total %6.3fs" % (name, dt), file=sys.stderr)
    finally:
        planner.settings.GRID_RESOLUTION_DEG = res_saved

    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=True)
        f.write("\n")

    if not synth_only:
        tot = sum(d for _, d, _ in order)
        fch = sum(x for _, _, x in order)
        print("-" * 64, file=sys.stderr)
        print("合计 %.3fs | 取数 %.3fs | 其余(搜索等) %.3fs" % (tot, fch, tot - fch), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
