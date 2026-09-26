"""A* 的量纲与启发式一致性。

2026-09-26 踩的坑，按严重程度排：
  ① 代价在度空间算、推时刻/里程用真实公里 —— 同一格 5.00 km × 4.32 km 被当正方形，
     东西向绕行被多收约 15%，软惩罚的「等效绕行公里」在横竖两个方向不是一个价。
  ② 把 ① 修掉之后 h 没跟着换：正方形 octile 对对角一跳估 7.07 km，而真实代价
     hypot(5.00, 4.32) = 6.61 km。启发式比它替代的那一步还贵 = 一致性断了，
     A* 退化成反复 relax 的 label-correcting，实测同一格点被重展开 12 次。
  ③ 直线偏离决胜项并进 h —— 它每跳可变，而修正 octile 在直的一跳上贴着真实代价
     （余量恰好为 0），任何扰动都直接把一致性顶破。它现在只作堆的次级键。
  ④ 节点量化取 2 位小数，而格距是 0.045° —— 格点被抖成 0.04/0.05 交替，斜着那一跳
     成了比格点分解更短的直线，一致性余量被吃掉 0.15 km，与 ①②③ 只差 1.7 倍，
     测试阈值因此定不出来。现在量化到 `_NODE_DECIMALS` 位，格距严格等距。

这里的测试不是为了"路径好看"，是为了让这四种退化不可能再无声发生。

运行：cd backend; .python/portable/python.exe tests/test_astar_metric.py
"""
import math
import os
import sys
import unittest

import numpy as np

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import planner  # noqa: E402
from planner import _deg_to_km, get_neighbors, heuristic  # noqa: E402

RES = 0.045


class TestHeuristicIsConsistent(unittest.TestCase):
    """一致性：h(a) ≤ 一跳代价 + h(邻居)。成立则首次弹出即最优 g，不该有重展开。

    两个抽样陷阱，都是这一版测试踩过之后改的：
      ① 直的一跳上余量**数学上恰好为 0**（修正 octile 贴着真实代价），单点抽样会
         把高估方向整个漏掉，必须沿格点系统扫；
      ② 目标点若全是偏东西向的远端城市，连正方形 octile 都能蒙混过关 —— 它的高估
         只在对角占优的剩余跨度上暴露，所以必须带一个 45° 方向的近目标。
    """

    TARGETS = [(31.15, 97.18), (29.27, 88.88), (30.65, 92.28)]

    # 直的一跳余量为 0，只容浮点噪声；被抓的三种退化形态余量在 -1.9e-3 ~ -6.2e-3
    EPS = 1e-9
    ANCHORS = [(29.65, 91.13), (30.4, 93.0)]

    def _anchors(self, a0):
        return [(round(a0[0] + i * RES, planner._NODE_DECIMALS),
                 round(a0[1] + j * RES, planner._NODE_DECIMALS))
                for i in range(-3, 4) for j in range(-3, 4)]

    def _quant(self, node):
        """与 plan_path_segment 主循环里对邻居做的那次量化完全一致。"""
        return (round(node[0], planner._NODE_DECIMALS), round(node[1], planner._NODE_DECIMALS))

    def _worst_slack(self, h, a0):
        """h(a, b, cosk)，cosk 按「这一段」固定 —— 与 plan_path_segment 的用法一致。"""
        worst = float('inf')
        for b in self.TARGETS:
            cosk = planner._search_cosk(a0[0], b[0])
            for a in self._anchors(a0):
                ha = h(a, b, cosk)
                for nb in [self._quant(n) for n in get_neighbors(a, RES)]:
                    if nb == a:
                        continue
                    worst = min(worst, (_deg_to_km(a, nb) / 111.0) + h(nb, b, cosk) - ha)
        return worst

    def test_lattice_spacing_is_uniform(self):
        """退化 ④：节点量化到 2 位会把 0.045° 的格距抖成 0.04/0.05 交替。

        抖动让斜跳变成一条比格点分解更短的直线，启发式余量被吃掉 0.15 km ——
        与它要抓的三种退化只差 1.7 倍，阈值根本定不出来。先把尺子本身钉直。
        这里沿搜索自己的走法一路量化下去（浮点误差是累加的），不是单步算好。
        """
        node = (29.65, 91.13)
        visited = [node[0]]
        for _ in range(8):
            node = self._quant(get_neighbors(node, RES)[0])    # 第一邻居 = 纬度 +res
            visited.append(node[0])
        diffs = [round(visited[i + 1] - visited[i], 9) for i in range(len(visited) - 1)]
        self.assertEqual(set(diffs), {round(RES, 9)},
                         "相邻格点间距不等，量化位数不够：%s" % diffs)

    def _at_res(self, fn):
        saved = planner.settings.GRID_RESOLUTION_DEG
        planner.settings.GRID_RESOLUTION_DEG = RES
        try:
            return fn()
        finally:
            planner.settings.GRID_RESOLUTION_DEG = saved

    def _worst_over_anchors(self, h):
        return min(self._worst_slack(h, a0) for a0 in self.ANCHORS)

    def _assert_caught(self, h, name):
        worst = self._worst_over_anchors(h)
        self.assertLess(worst, -self.EPS,
                        "%s 应当被判负，但抽样没抓到（测试失效了）" % name)

    def test_never_exceeds_one_hop_plus_neighbor(self):
        def check():
            for a0 in self.ANCHORS:
                worst = self._worst_slack(planner.heuristic, a0)
                self.assertGreaterEqual(
                    worst, -self.EPS,
                    "启发式对某一跳高估（最差余量 %+.6f 等效度 = %+.2f km）"
                    % (worst, worst * 111))
        self._at_res(check)

    # ---- 以下四条是「测试自己的测试」：已知会退化的形态必须被抓到 ----

    def test_catches_square_octile_in_degree_space(self):
        """退化 ①：度空间正方形 octile 配真实公里代价。"""
        def h(a, b, cosk):
            dx = abs(b[0] - a[0])
            dy = abs(b[1] - a[1])
            return max(dx, dy) + (math.sqrt(2) - 1) * min(dx, dy)
        self._at_res(lambda: self._assert_caught(h, "度空间正方形 octile"))

    def test_catches_octile_with_cos_but_square_diagonal(self):
        """退化 ②：只换了尺子没换 h —— 实验里 12 倍重展开那一版。"""
        def h(a, b, cosk):
            dx = abs(b[0] - a[0])
            dy = abs(b[1] - a[1]) * cosk
            return max(dx, dy) + (math.sqrt(2) - 1) * min(dx, dy)
        self._at_res(lambda: self._assert_caught(h, "cos 折算但仍是正方形 octile"))

    def test_catches_bias_term_inside_heuristic(self):
        """退化 ③：直线偏离项并进 h。"""
        def h(a, b, cosk):
            s = (29.27, 88.88)
            return planner.heuristic(a, b, cosk) + abs(
                (a[0] - b[0]) * (s[1] - b[1]) * cosk
                - (a[1] - b[1]) * cosk * (s[0] - b[0])) * 0.01
        self._at_res(lambda: self._assert_caught(h, "直线偏离项并进 h"))

    def test_no_start_argument(self):
        """h 不接受 start：偏离项并进 h 就是重展开的成因，签名先钉住。"""
        with self.assertRaises(TypeError):
            heuristic((29.65, 91.13), (31.15, 97.18), start=(29.27, 88.88))


class TestMetricIsIsotropic(unittest.TestCase):
    """一格在 30°N 是 5.00 km（南北）× 4.32 km（东西），代价与 h 都必须按真实形状算。"""

    def test_east_west_cell_is_shorter_than_north_south(self):
        a = (30.0, 91.0)
        ns = _deg_to_km(a, (a[0] + RES, a[1]))
        ew = _deg_to_km(a, (a[0], a[1] + RES))
        self.assertAlmostEqual(ns, 4.995, places=2)
        self.assertAlmostEqual(ew, ns * math.cos(math.radians(30.0)), places=2)
        self.assertLess(ew, ns)

    def test_heuristic_is_linear_in_cell_count(self):
        """同一轴向走 n 格，下界就该是 n 倍单格下界 —— 不能被 max/min 分支折弯。"""
        a = (30.0, 91.0)
        cosk = planner._search_cosk(a[0], a[0])
        for step in ((RES, 0.0), (0.0, RES), (RES, RES)):
            one = heuristic(a, (a[0] + step[0], a[1] + step[1]), cosk)
            five = heuristic(a, (a[0] + 5 * step[0], a[1] + 5 * step[1]), cosk)
            self.assertAlmostEqual(five / one, 5.0, places=6)

    def test_east_west_lower_bound_is_scaled_by_cos(self):
        a = (30.0, 91.0)
        cosk = planner._search_cosk(a[0], a[0])
        ns_km = heuristic(a, (a[0] + RES, a[1]), cosk) * 111.0
        ew_km = heuristic(a, (a[0], a[1] + RES), cosk) * 111.0
        self.assertAlmostEqual(ns_km, RES * 111.0, places=6)
        self.assertAlmostEqual(ew_km, RES * cosk * 111.0, places=6)

    def test_diagonal_is_not_root_two_of_cardinal(self):
        """正方形 octile 把对角估成 √2×直跳；真实长方形上是 hypot(南北格, 东西格)。"""
        a = (30.0, 91.0)
        cosk = planner._search_cosk(a[0], a[0])
        diag_km = heuristic(a, (a[0] + RES, a[1] + RES), cosk) * 111.0
        self.assertAlmostEqual(diag_km, math.hypot(RES * 111.0, RES * cosk * 111.0), places=6)
        self.assertLess(diag_km, math.sqrt(2) * RES * 111.0)

    def test_search_cosk_is_conservative_over_the_segment(self):
        """固定折算因子必须 ≤ 段内任何纬度的 cos，否则东西向代价被高估。"""
        cosk = planner._search_cosk(29.27, 31.15)
        for lat in (29.27, 30.0, 31.15, 33.15):
            self.assertLessEqual(cosk, math.cos(math.radians(lat)) + 1e-12)


class _CleanLoader:
    """净空网格：TKE/EDR 全 0，软惩罚不参与，于是累计代价必须等于累计里程。"""

    def get_meteorology_grid(self, min_lat, max_lat, min_lon, max_lon, date=None):
        shape = (48, 21, 21)
        zeros = np.zeros(shape)
        lats = np.linspace(min_lat, max_lat, shape[1])
        lons = np.linspace(min_lon, max_lon, shape[2])
        times = np.arange(shape[0]) * np.timedelta64(1, 'h') + np.datetime64('2022-07-01T00:00')
        return zeros, zeros.copy(), lats, lons, times


class TestPlanCostUnit(unittest.TestCase):
    """端到端锁量纲：净空线上没有惩罚，g 与里程必须一位不差。"""

    def _run(self, start, end, res):
        saved_loader = planner.data_loader
        saved_res = planner.settings.GRID_RESOLUTION_DEG
        planner.data_loader = _CleanLoader()
        planner.settings.GRID_RESOLUTION_DEG = res
        try:
            return planner.plan_path_segment(
                start, end, tke_thresh=1.0, edr_thresh=0.3,
                start_time_str="2022-07-01T08:00",
                initial_g_score=0.0, initial_dist=0.0, speed_kmh=60.0)
        finally:
            planner.data_loader = saved_loader
            planner.settings.GRID_RESOLUTION_DEG = saved_res

    def test_grid_cost_equals_distance_in_clean_air(self):
        """旧口径在这条线上 g=358.99 而里程 311.71 —— 多收的 15% 全是长方格当正方格。"""
        seg = self._run((30.0, 90.0), (29.0, 93.0), 0.5)
        self.assertTrue(seg.found)
        self.assertGreater(seg.final_dist, 0.0)
        self.assertAlmostEqual(seg.final_g_score, seg.final_dist, places=9,
                               msg="累计 A* 代价与真实里程脱钩：距离项又回到度空间算了")

    def test_clean_air_expansions_stay_near_path_length(self):
        """一致启发式 + 净空：展开数不该远超路径长度（不一致时实测 12 次/格点）。

        分母用**格数**而不是 `len(seg.path)`：路径拉直（`PATH_PULL_ENABLED`）之后
        返回的是几条直线段，点数与搜索规模再无关系（净空下 2 点 vs 23 次展开）。
        """
        a, b = (30.0, 90.0), (28.0, 94.0)
        res = 0.5
        cells = planner._deg_to_km(a, b) / (111.0 * res)
        seg = self._run(a, b, res)
        self.assertTrue(seg.found)
        self.assertLess(seg.iterations, 4 * cells,
                        "展开数远超路径格数，启发式可能又不一致了：iterations=%d，格数 %.1f"
                        % (seg.iterations, cells))


if __name__ == "__main__":
    unittest.main()
