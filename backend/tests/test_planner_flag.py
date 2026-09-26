"""planner 的 found / 失败原因分支测试。

计划 §9.1 要求验证「plan_path 的 found=True/False 区分（含封死场景）」。
这里用合成的气象网格注入，避免读取 110GB 真实数据；重点不是 A* 的
寻路细节，而是**失败时不得伪装成直线路径**这一行为契约。
"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import planner  # noqa: E402
from planner import PlanResult, SegmentResult  # noqa: E402


def _make_grid(value: float, shape=(5, 5, 5)):
    """构造一个恒定的 TKE/EDR 网格（时间 × 纬 × 经）。"""
    arr = np.full(shape, value, dtype=float)
    return arr


class _StubLoader:
    """替身 data_loader：返回预设网格，避免触碰真实 NetCDF。"""

    def __init__(self, tke_value: float, edr_value: float = 0.0):
        self.tke_value = tke_value
        self.edr_value = edr_value

    def get_meteorology_grid(self, min_lat, max_lat, min_lon, max_lon, date=None):
        shape = (48, 21, 21)  # 48 小时 × 0.1° 网格
        tke = _make_grid(self.tke_value, shape)
        edr = _make_grid(self.edr_value, shape)
        lats = np.linspace(min_lat, max_lat, shape[1])
        lons = np.linspace(min_lon, max_lon, shape[2])
        times = np.arange(shape[0]) * np.timedelta64(1, 'h') + np.datetime64('2022-07-01T00:00')
        return tke, edr, lats, lons, times


class _WallLoader:
    """一道横在经 92°、只在南缘留缺口的湍流墙（全时段都封着，不随时间开合）。

    网格 0.2° 间隔，与用例里的 GRID_RESOLUTION_DEG 对齐，避免跳格错位引入 NaN。
    """

    SHAPE = (48, 21, 21)

    def get_meteorology_grid(self, min_lat, max_lat, min_lon, max_lon, date=None):
        tke = np.zeros(self.SHAPE, dtype=float)
        idx_lon = int(np.abs(np.linspace(min_lon, max_lon, self.SHAPE[2]) - 92.0).argmin())
        tke[:, :, idx_lon] = 5.0
        tke[:, :2, idx_lon] = 0.0        # 南缘两行留缺口
        lats = np.linspace(min_lat, max_lat, self.SHAPE[1])
        lons = np.linspace(min_lon, max_lon, self.SHAPE[2])
        times = np.arange(self.SHAPE[0]) * np.timedelta64(1, 'h') + np.datetime64('2022-07-01T00:00')
        return tke, np.zeros_like(tke), lats, lons, times


class TestPlannerFoundFlag(unittest.TestCase):
    def setUp(self):
        self._orig_loader = getattr(planner, "data_loader", None)
        self._orig_resolution = planner.settings.GRID_RESOLUTION_DEG

    def tearDown(self):
        if self._orig_loader is not None:
            planner.data_loader = self._orig_loader
        planner.settings.GRID_RESOLUTION_DEG = self._orig_resolution

    def _install(self, tke_value, edr_value=0.0, resolution=0.5):
        planner.data_loader = _StubLoader(tke_value, edr_value)
        planner.settings.GRID_RESOLUTION_DEG = resolution

    def test_segment_blocked_when_start_unsafe(self):
        """起点本身在硬阈值内 → found=False，且不得返回直线路径。"""
        self._install(tke_value=5.0)  # 远超 TKE 硬阈值 1.0
        seg = planner.plan_path_segment(
            (29.65, 91.13), (29.85, 94.36),
            tke_thresh=1.0, edr_thresh=0.3,
            start_time_str="2022-07-01T08:00",
            initial_g_score=0.0, initial_dist=0.0, speed_kmh=60.0,
        )
        self.assertIsInstance(seg, SegmentResult)
        self.assertFalse(seg.found)
        self.assertEqual(seg.path, [])
        self.assertEqual(seg.reason, 'blocked')

    def test_segment_blocked_by_edr(self):
        """EDR 命中硬约束同样判为 blocked。"""
        self._install(tke_value=0.0, edr_value=0.9)
        seg = planner.plan_path_segment(
            (29.65, 91.13), (29.85, 94.36),
            tke_thresh=1.0, edr_thresh=0.3,
            start_time_str="2022-07-01T08:00",
            initial_g_score=0.0, initial_dist=0.0, speed_kmh=60.0,
        )
        self.assertFalse(seg.found)

    def test_segment_found_in_clear_air(self):
        """气象条件良好时应能规划出路径，且 found=True。"""
        self._install(tke_value=0.0)
        seg = planner.plan_path_segment(
            (29.65, 91.13), (29.85, 93.5),
            tke_thresh=1.0, edr_thresh=0.3,
            start_time_str="2022-07-01T08:00",
            initial_g_score=0.0, initial_dist=0.0, speed_kmh=60.0,
        )
        self.assertTrue(seg.found)
        self.assertGreater(len(seg.path), 0)
        self.assertIsNone(seg.reason)

    def test_failed_segment_has_no_placeholder_straight_line(self):
        """失败时 path 必须为空——这是「不再静默回退直线」的核心契约。"""
        self._install(tke_value=9.0)
        seg = planner.plan_path_segment(
            (29.65, 91.13), (29.85, 94.36),
            tke_thresh=1.0, edr_thresh=0.3,
            start_time_str="2022-07-01T08:00",
            initial_g_score=0.0, initial_dist=0.0, speed_kmh=60.0,
        )
        self.assertFalse(seg.found)
        self.assertEqual(len(seg.path), 0, "失败路径不得用直线兜底填充")


class TestExpansionBudget(unittest.TestCase):
    """展开预算随该段直线距离伸缩（旧实现写死 20000，长线连搜索框都扫不完）。"""

    def setUp(self):
        s = planner.settings
        self._saved = (s.ASTAR_EXPANSIONS_PER_KM, s.ASTAR_MIN_EXPANSIONS, s.ASTAR_MAX_EXPANSIONS)
        self._saved_res = s.GRID_RESOLUTION_DEG
        self._saved_loader = planner.data_loader

    def tearDown(self):
        s = planner.settings
        s.ASTAR_EXPANSIONS_PER_KM, s.ASTAR_MIN_EXPANSIONS, s.ASTAR_MAX_EXPANSIONS = self._saved
        s.GRID_RESOLUTION_DEG = self._saved_res
        planner.data_loader = self._saved_loader

    def test_scales_with_distance_and_respects_bounds(self):
        s = planner.settings
        s.ASTAR_EXPANSIONS_PER_KM, s.ASTAR_MIN_EXPANSIONS, s.ASTAR_MAX_EXPANSIONS = 50.0, 20000, 120000
        self.assertEqual(planner.expansion_budget(100.0), 20000, "短线不得比旧写死值更少")
        self.assertEqual(planner.expansion_budget(824.0), 41200)
        self.assertEqual(planner.expansion_budget(5000.0), 120000, "必须有上限兜住公网响应")
        self.assertEqual(planner.expansion_budget(-10.0), 20000)
        self.assertGreater(planner.expansion_budget(900.0), planner.expansion_budget(300.0))

    def test_segment_search_reads_the_budget(self):
        """把预算压到 5 次：同一条净空航线也该报「没搜完」，而不是「没有路」。"""
        s = planner.settings
        s.ASTAR_EXPANSIONS_PER_KM, s.ASTAR_MIN_EXPANSIONS, s.ASTAR_MAX_EXPANSIONS = 0.01, 5, 5
        planner.data_loader = _StubLoader(0.0, 0.0)
        s.GRID_RESOLUTION_DEG = 0.5
        args = dict(tke_thresh=1.0, edr_thresh=0.3, start_time_str="2022-07-01T08:00",
                    initial_g_score=0.0, initial_dist=0.0, speed_kmh=60.0)
        starved = planner.plan_path_segment((29.65, 91.13), (29.85, 94.36), **args)
        self.assertFalse(starved.found)
        self.assertEqual(starved.reason, 'iteration_limit')
        self.assertLessEqual(starved.iterations, 5)

        s.ASTAR_EXPANSIONS_PER_KM, s.ASTAR_MIN_EXPANSIONS, s.ASTAR_MAX_EXPANSIONS = 50.0, 20000, 120000
        roomy = planner.plan_path_segment((29.65, 91.13), (29.85, 94.36), **args)
        self.assertTrue(roomy.found, "同一条线只是给了预算就该出解 —— 说明失败原因是预算不是湍流")


class TestNoRepeatedExpansion(unittest.TestCase):
    """「我来过」标记：堆里的陈旧副本不得再展开，也不得吃掉展开预算。

    换标记前后 F 值排序、路径与代价都不许变（金标 g_before/g_after 已覆盖），
    这里锁的是另一件事 —— 同一个节点被展开两次，既白烧预算又白白拖慢。
    用例故意把缺口放到航线以南，让搜索先横扫整面墙再南下，这样才会出现
    一个节点被多条路径反复改进、堆里同时躺着好几份副本的情形。
    """

    def setUp(self):
        self._saved_loader = planner.data_loader
        self._saved_res = planner.settings.GRID_RESOLUTION_DEG
        planner.data_loader = _WallLoader()
        planner.settings.GRID_RESOLUTION_DEG = 0.2

    def tearDown(self):
        planner.data_loader = self._saved_loader
        planner.settings.GRID_RESOLUTION_DEG = self._saved_res

    def test_each_node_expanded_at_most_once(self):
        expanded = []
        pops = []
        real_neighbors = planner.get_neighbors
        real_pop = planner.heapq.heappop

        def spy_neighbors(node, resolution):
            expanded.append(node)
            return real_neighbors(node, resolution)

        def spy_pop(heap):
            item = real_pop(heap)
            pops.append(item[1])
            return item

        planner.get_neighbors = spy_neighbors
        planner.heapq.heappop = spy_pop
        try:
            seg = planner.plan_path_segment(
                (30.0, 90.0), (30.0, 94.0),
                tke_thresh=1.0, edr_thresh=0.3,
                start_time_str="2022-07-01T08:00",
                initial_g_score=0.0, initial_dist=0.0, speed_kmh=60.0,
            )
        finally:
            planner.get_neighbors = real_neighbors
            planner.heapq.heappop = real_pop

        self.assertTrue(seg.found, "绕过墙找缺口应当有解")
        self.assertGreater(len(expanded), 100, "用例得真的逼搜索横扫一遍墙面")
        # ① 没有节点被展开两次
        self.assertEqual(len(expanded), len(set(expanded)))
        # ② 确实出现过陈旧副本（同一个节点被弹出多次），否则下面两条测不到东西
        self.assertGreater(len(pops), len(set(pops)))
        self.assertGreater(len(pops), seg.iterations, "陈旧副本不得计入展开预算")
        # ③ iterations 的口径是「研究过的路口」：命中终点那一次弹出没有邻居循环，
        #    所以它等于展开数 + 未展开就返回/判末段失败的弹出数
        self.assertGreaterEqual(seg.iterations, len(expanded))


class TestSearchEffortOf(unittest.TestCase):
    """「研究过的路口 / 预算」这对数：整条线按段求和，没跑到搜索时不给。"""

    @staticmethod
    def _plan(segs):
        return PlanResult(path=[], total_distance=0.0, total_grid_cost=0.0,
                          found=False, reason='iteration_limit', segment_results=segs)

    def _seg(self, iters, budget):
        return SegmentResult(path=[], found=False, reason='iteration_limit',
                             iterations=iters, expansion_budget=budget)

    def test_sums_over_segments(self):
        got = planner.search_effort_of(self._plan([self._seg(100, 20000), self._seg(50, 20000)]))
        self.assertEqual(got, {"expansions": 150, "budget": 40000})

    def test_no_budget_means_no_number(self):
        """起点被封 / 无数据时压根没进搜索，报「0 / 0」会被读成「一秒就没搜完」。"""
        self.assertIsNone(planner.search_effort_of(self._plan([self._seg(0, 0)])))
        self.assertIsNone(planner.search_effort_of(self._plan([])))


class TestPlanResultDefaults(unittest.TestCase):
    def test_plan_result_failure_shape(self):
        """PlanResult 在失败时应带 found=False 与原因，供上层判定。"""
        res = PlanResult(
            path=[], total_distance=0.0, total_grid_cost=0.0,
            found=False, reason='blocked', times=[], tkes=[], edrs=[],
            waypoints_reached=[], segment_results=[],
        )
        self.assertFalse(res.found)
        self.assertEqual(res.reason, 'blocked')
        self.assertEqual(res.path, [])

    def test_plan_result_success_shape(self):
        res = PlanResult(
            path=[(29.65, 91.13), (29.85, 94.36)], total_distance=300.0,
            total_grid_cost=1.0, found=True, reason=None,
            times=[], tkes=[0.1, 0.2], edrs=[0.0, 0.0],
            waypoints_reached=[1], segment_results=[],
        )
        self.assertTrue(res.found)
        self.assertIsNone(res.reason)
        self.assertEqual(len(res.path), 2)


if __name__ == "__main__":
    unittest.main()
