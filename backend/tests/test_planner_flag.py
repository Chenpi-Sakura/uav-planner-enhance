"""planner 的 found / 失败原因分支测试。

计划 §9.1 要求验证「plan_path 的 found=True/False 区分（含封死场景）」。
这里用合成的气象网格注入，避免读取 110GB 真实数据；重点不是 A* 的
寻路细节，而是**失败时不得伪装成直线路径**这一行为契约。
"""
import math
import sys
import time
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
    墙内取值默认 5.0（配 tke_thresh=1.0 是硬障碍）；传 12.0 再配硬阈值 26.0，
    它就变成「合法但很贵」的那条带 —— 拉直必须拒绝穿它。
    """

    SHAPE = (48, 21, 21)

    def __init__(self, tke_value: float = 5.0):
        self.tke_value = tke_value

    def get_meteorology_grid(self, min_lat, max_lat, min_lon, max_lon, date=None):
        tke = np.zeros(self.SHAPE, dtype=float)
        idx_lon = int(np.abs(np.linspace(min_lon, max_lon, self.SHAPE[2]) - 92.0).argmin())
        tke[:, :, idx_lon] = self.tke_value
        tke[:, :2, idx_lon] = 0.0        # 南缘两行留缺口
        lats = np.linspace(min_lat, max_lat, self.SHAPE[1])
        lons = np.linspace(min_lon, max_lon, self.SHAPE[2])
        times = np.arange(self.SHAPE[0]) * np.timedelta64(1, 'h') + np.datetime64('2022-07-01T00:00')
        return tke, np.zeros_like(tke), lats, lons, times


class _BandLoader:
    """一块有边界的湍流区：绕过去很便宜，穿过去**合法但很贵**。

    与 _WallLoader 的分工：墙配低阈值测「硬障碍前不许拉」，这块默认 TKE 24 的区
    配硬阈值 26 测「直连过得了硬阈值、代价却翻几倍时也不许拉」。
    按经纬度范围划定而不按行号 —— 网格行号随包围盒变，写死行号等于把用例绑在几何上。
    """

    SHAPE = (48, 21, 21)

    def __init__(self, tke_value: float = 24.0, lat_range=(29.0, 30.5),
                 lon_range=(91.5, 92.6)):
        self.tke_value = tke_value
        self.lat_range = lat_range
        self.lon_range = lon_range

    def get_meteorology_grid(self, min_lat, max_lat, min_lon, max_lon, date=None):
        lats = np.linspace(min_lat, max_lat, self.SHAPE[1])
        lons = np.linspace(min_lon, max_lon, self.SHAPE[2])
        in_lat = (lats >= self.lat_range[0]) & (lats <= self.lat_range[1])
        in_lon = (lons >= self.lon_range[0]) & (lons <= self.lon_range[1])
        tke = np.zeros(self.SHAPE, dtype=float)
        tke[:, np.outer(in_lat, in_lon)] = self.tke_value
        times = np.arange(self.SHAPE[0]) * np.timedelta64(1, 'h') + np.datetime64('2022-07-01T00:00')
        return tke, np.zeros_like(tke), lats, lons, times


class _WindowLoader:
    """一块只在某几个小时里颠簸的区 —— 走得快反而踩进颠簸。

    专门给拉直用的陷阱：线一短，沿程每个点的到达时刻都整体提前，而 A* 当初是按
    【晚若干小时的那一帧】把这些格点验过的。这个场让"早到"违法、"晚到"合法，
    于是只有全线按新时刻重判过，才不会把一条越过硬阈值的线当成可飞交出去。
    其余替身都是全天恒定的场，测不到这一类时刻漂移。
    """

    SHAPE = (48, 21, 21)

    def __init__(self, tke_value: float = 40.0, hot_hours=range(12, 16),
                 lat_range=(28.6, 29.4), lon_range=(93.4, 94.2)):
        self.tke_value = tke_value
        self.hot_hours = list(hot_hours)
        self.lat_range = lat_range
        self.lon_range = lon_range

    def get_meteorology_grid(self, min_lat, max_lat, min_lon, max_lon, date=None):
        lats = np.linspace(min_lat, max_lat, self.SHAPE[1])
        lons = np.linspace(min_lon, max_lon, self.SHAPE[2])
        box = np.outer((lats >= self.lat_range[0]) & (lats <= self.lat_range[1]),
                       (lons >= self.lon_range[0]) & (lons <= self.lon_range[1]))
        tke = np.zeros(self.SHAPE, dtype=float)
        for h in self.hot_hours:
            tke[h] = np.where(box, self.tke_value, 0.0)
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


class TestTimeLimit(unittest.TestCase):
    """墙钟先到要报 time_limit，不能顶着一句「用满预算」冒充 iteration_limit。

    两者对用户都是「未算出」，分开的是那句解释：iteration_limit 是这条线没算完，
    time_limit 是这台机器这一刻慢 —— 混起来就等于把 I/O 慢说成没有航线。
    计时从开始展开起算，气象取数不在内（否则冷读第一次点击必然被判未算出）。
    """

    def setUp(self):
        self._saved_loader = planner.data_loader
        self._saved_res = planner.settings.GRID_RESOLUTION_DEG
        self._saved_limit = planner.settings.ASTAR_TIME_LIMIT_S
        planner.data_loader = _WallLoader()
        # 0.1° 让这条绕墙线要展开 ~730 次：检查粒度是 256，用例得明显跨过它
        planner.settings.GRID_RESOLUTION_DEG = 0.1

    def tearDown(self):
        planner.data_loader = self._saved_loader
        planner.settings.GRID_RESOLUTION_DEG = self._saved_res
        planner.settings.ASTAR_TIME_LIMIT_S = self._saved_limit

    def _search(self, **kw):
        return planner.plan_path_segment(
            (30.0, 90.0), (30.0, 94.0),
            tke_thresh=1.0, edr_thresh=0.3,
            start_time_str="2022-07-01T08:00",
            initial_g_score=0.0, initial_dist=0.0, speed_kmh=60.0, **kw)

    def test_clock_stop_reports_time_limit(self):
        planner.settings.ASTAR_TIME_LIMIT_S = 0.0
        seg = self._search()
        self.assertFalse(seg.found)
        self.assertEqual(seg.reason, 'time_limit')
        # 检查粒度是 256 次展开：到点就停，绝不能把两万次预算烧完
        self.assertLess(seg.iterations, 512, "墙钟到点后仍在展开，说明检查没生效")
        self.assertLess(seg.iterations, seg.expansion_budget)
        self.assertGreaterEqual(seg.elapsed_s, 0.0)

    def test_shared_deadline_wins_over_local_limit(self):
        """多段航线共用一份余额：本段自己的 18 s 没到，整次规划的时刻已经过了。"""
        planner.settings.ASTAR_TIME_LIMIT_S = 600.0
        seg = self._search(deadline=time.monotonic() - 1.0)
        self.assertEqual(seg.reason, 'time_limit')
        self.assertLess(seg.iterations, 512)

    def test_converged_search_still_reports_elapsed(self):
        planner.settings.ASTAR_TIME_LIMIT_S = 600.0
        seg = self._search()
        self.assertTrue(seg.found, "绕墙找缺口这条线本来就有解")
        self.assertIsNone(seg.reason)
        self.assertGreater(seg.elapsed_s, 0.0)


class TestPathPull(unittest.TestCase):
    """网格折线的拉直：A* 定拓扑，这一遍还原几何。

    三条判据各测一件事：净空要拉成直线（拿回那 7% 的网格税）、硬墙前不许拉、
    **合法但更贵**的直连也不许拉。最后一条是实测踩出来的坑：
    大风天那条 824 km 的两点直线过得了硬阈值（TKE 峰值 21.5 < 26），
    代价却从 1128 涨到 4261 —— 只判"能不能过"就会把一条好线拉成穿湍流的线。
    """

    RES = 0.5
    A = (30.0, 90.0)
    B = (29.0, 94.0)          # 浅角度：8 向网格只能走 0/45/90，阶梯最明显

    def setUp(self):
        self._saved_loader = planner.data_loader
        self._saved_res = planner.settings.GRID_RESOLUTION_DEG
        self._saved_pull = planner.settings.PATH_PULL_ENABLED
        planner.settings.GRID_RESOLUTION_DEG = self.RES

    def tearDown(self):
        planner.data_loader = self._saved_loader
        planner.settings.GRID_RESOLUTION_DEG = self._saved_res
        planner.settings.PATH_PULL_ENABLED = self._saved_pull

    def _seg(self, loader, tke_thresh, pull):
        planner.data_loader = loader
        planner.settings.PATH_PULL_ENABLED = pull
        return planner.plan_path_segment(
            self.A, self.B, tke_thresh, 0.9, "2022-07-01T08:00",
            0.0, 0.0, 60.0)

    def _refs(self, pts, loader, hard, res=None):
        """给一条折线算出配套的 g/d 序列 —— 用被测的同一把尺子，免得两套口径。"""
        res = self.RES if res is None else res
        lat0 = min(p[0] for p in pts) - planner.SEGMENT_PAD_DEG
        lat1 = max(p[0] for p in pts) + planner.SEGMENT_PAD_DEG
        lon0 = min(p[1] for p in pts) - planner.SEGMENT_PAD_DEG
        lon1 = max(p[1] for p in pts) + planner.SEGMENT_PAD_DEG
        tke, edr, la, lo, ts = loader.get_meteorology_grid(lat0, lat1, lon0, lon1)
        lk = planner._GridIndex(la, lo, ts, strict=False)
        base = planner._ns_of(np.datetime64("2022-07-01T08:00"))
        sample = 111.0 * res / 2.0
        g, d = [0.0], [0.0]
        for i in range(1, len(pts)):
            cost, km, blocked = planner._straight_hop(
                pts[i - 1], pts[i], base, d[-1], 60.0, tke, edr, lk,
                hard, 0.9, 1.0, sample, 111.0 * res)
            self.assertFalse(blocked, "折线本身得合法，否则用例不成立")
            g.append(g[-1] + cost)
            d.append(d[-1] + km)
        return dict(tke=tke, edr=edr, lk=lk, base=base, g=g, d=d, res=res)

    def _pull(self, pts, r, hard):
        return planner._pull_path(pts, r["g"], r["d"], r["base"], 60.0, r["tke"],
                                  r["edr"], r["lk"], hard, 0.9, 1.0, r["res"])

    @staticmethod
    def _off_line_km(p, a, b):
        """点 p 到直线 a→b 的垂距（km）。等距圆柱近似，与 _deg_to_km 同一把尺子。"""
        k = math.cos(math.radians((a[0] + b[0]) / 2.0))
        bx, by = (b[1] - a[1]) * k, b[0] - a[0]
        px, py = (p[1] - a[1]) * k, p[0] - a[0]
        ln = math.hypot(bx, by)
        return abs(bx * py - by * px) / ln * 111.0 if ln else 0.0

    @staticmethod
    def _max_gap_km(pts):
        return max((planner._deg_to_km(pts[i - 1], pts[i]) for i in range(1, len(pts))),
                   default=0.0)

    def test_clean_air_pulls_the_staircase_into_a_straight_line(self):
        grid = self._seg(_StubLoader(0.0), 1.0, pull=False)
        pulled = self._seg(_StubLoader(0.0), 1.0, pull=True)
        self.assertTrue(grid.found and pulled.found)
        self.assertGreater(len(grid.path), 4, "用例得先真的产出一条阶梯折线")
        gp = [(p.lat, p.lon) for p in pulled.path]
        self.assertGreater(len(gp), 3, "拉直不该把整条线塌成两个端点")
        off = max(self._off_line_km(p, self.A, self.B) for p in gp)
        self.assertLess(off, 0.05, "净空下没拉成直线，判据或采点错了")

        straight = planner._haversine_km(self.A[0], self.A[1], self.B[0], self.B[1])
        self.assertLess(pulled.final_dist, grid.final_dist, "拉直没拿回里程")
        self.assertAlmostEqual(pulled.final_dist, straight, delta=2.0)
        self.assertAlmostEqual(pulled.final_g_score, pulled.final_dist, places=6,
                               msg="净空时代价该等于里程，脱钩说明拉直另算了一套尺子")

    def test_pulled_polyline_keeps_vertex_density(self):
        """拉直只改形状，不能改采样密度 —— 下游是按**折线顶点**算风险的。

        M5 实测踩过：path 只剩 2 个点后，「沿程最大 TKE」退化成两端点的 TKE，
        以城市为间隔点的区域飞行等级从 2~3 段塌成 1 段。
        """
        pulled = self._seg(_StubLoader(0.0), 1.0, pull=True)
        gp = [(p.lat, p.lon) for p in pulled.path]
        # 网格自己的对角步长就有 √2 格距，比这更稀就说明中途没人采样了
        self.assertLessEqual(self._max_gap_km(gp), math.sqrt(2) * 111.0 * self.RES * 1.05)
        self.assertGreaterEqual(len(gp), int(pulled.final_dist / (2.0 * 111.0 * self.RES)))
        # 累计里程必须与顶点间距自洽：末点时刻是按 cum 推的
        self.assertEqual(len(gp), len(pulled.times))
        self.assertEqual(len(gp), len(pulled.tkes))

    def test_pull_must_not_arrive_earlier_into_turbulence(self):
        """拉直提前了到达时刻，就不能把「A* 按晚一帧天气验过的格点」原样交出去。

        这条是 2026-09-26 实测抓到的：昌都→日喀则方向一条 397 km 的拉直线，
        沿程 TKE 峰值 35.18 已越过硬阈值 26，却仍被当成可飞 —— 因为拉直只重验了
        新拉的直连，没重验它保留下来的那些网格段。判据是每个顶点按自己的新时刻重判，
        不过就把造成这次提前的那条弦撤掉（本例里所有弦都撤光，等于退回网格线）。
        """
        grid = self._seg(_WindowLoader(), 26.0, pull=False)
        pulled = self._seg(_WindowLoader(), 26.0, pull=True)
        self.assertTrue(grid.found, "用例不成立：连网格线都没规划出来")
        self.assertGreater(len(grid.path), 4)
        # 拉直后的时刻会踩进 12:00-16:00 那块颠簸区 → 必须原样退回网格线
        self.assertEqual([(p.lat, p.lon) for p in pulled.path],
                         [(p.lat, p.lon) for p in grid.path],
                         "拉直交出了一条按新时刻已经不合法的线")
        self.assertAlmostEqual(pulled.final_dist, grid.final_dist, places=6)
        self.assertTrue(all(t < 26.0 for t in pulled.tkes), "交付线上有人越过硬阈值")

    def test_one_bad_chord_keeps_the_earlier_gains(self):
        """一根坏弦只该撤掉它自己，不该把前面已经验过的收益一起抹掉。

        这块窗在 13:00-15:00 封住末段附近：网格线按 08:00 起算、465 km 走完，
        那些点落在 16 时之后是安全的；把末段前那条弦拉直、线一短，同一批格点就
        挪进了开窗时间里。2026-09-26 对照 HEAD 的实现实测：
        旧（整条作废）交回 465.45 km 的网格线，新（按弦回退）交回 457.51 km 的混合线
        —— 前缀那几条弦的收益留着，出事的只是末段那一条。
        """
        planner.data_loader = _WindowLoader(tke_value=40.0, hot_hours=range(13, 16),
                                            lat_range=(28.4, 29.1),
                                            lon_range=(93.4, 94.3))
        planner.settings.PATH_PULL_ENABLED = False
        grid = planner.plan_path_segment(self.A, self.B, 26.0, 0.9, "2022-07-01T08:00",
                                         0.0, 0.0, 60.0)
        planner.settings.PATH_PULL_ENABLED = True
        pulled = planner.plan_path_segment(self.A, self.B, 26.0, 0.9, "2022-07-01T08:00",
                                           0.0, 0.0, 60.0)
        self.assertTrue(grid.found and pulled.found)

        straight = planner._haversine_km(self.A[0], self.A[1], self.B[0], self.B[1])
        self.assertLess(pulled.final_dist, grid.final_dist - 1.0,
                        "按弦回退没拿回任何里程，等于还在整条作废")
        self.assertGreater(pulled.final_dist, straight + 1.0,
                           "用例不成立：这条线被拉到底了，中间没有保留段可撤")

        pts = [(p.lat, p.lon) for p in pulled.path]
        self.assertEqual(pts[0], self.A)
        self.assertEqual(pts[-1], self.B)
        # tkes 是交付时【按各点自己的到达时刻】读出来的，越界就是越界
        self.assertEqual(len(pts), len(pulled.tkes))
        self.assertTrue(all(t == t and t < 26.0 for t in pulled.tkes),
                        "按弦回退换来了一条不合法的线：%s" % max(pulled.tkes))
        # 撤掉的是末段那一条，前缀的拉动还在：混合线的顶点间距不超过网格步长
        self.assertLessEqual(self._max_gap_km(pts), math.sqrt(2) * 111.0 * self.RES * 1.05)

    def test_retained_grid_stretch_keeps_the_shortened_clock(self):
        """拉掉一段之后，保留下来的那段网格线必须按【缩短后的】里程推时刻。

        实测踩过：昌都→日喀则方向那条 397 km 的线，后半程照抄了 1013 km 网格线的
        累计里程，于是那些点是按晚 10 小时的一帧天气判的 —— 恰好绕过拉直自己的
        判据（"用旧时刻复检等于没复检"）。混合线（一半拉直、一半网格）才会暴露，
        全拉直与全不拉直的用例都测不到。
        """
        seg = self._seg(_WallLoader(), 1.0, pull=True)
        self.assertTrue(seg.found)
        pts = [(p.lat, p.lon) for p in seg.path]
        self.assertGreater(len(pts), 4, "用例得真的产出「拉掉一段 + 保留一段」的混合线")
        cum = [0.0]
        for i in range(1, len(pts)):
            cum.append(cum[-1] + planner._deg_to_km(pts[i - 1], pts[i]))
        # 交付的总里程与每个点的到达时刻，都必须跟着【画出来的那条折线】走
        self.assertAlmostEqual(cum[-1], seg.final_dist, delta=1.0,
                               msg="累计里程与几何脱钩：保留段照抄了网格线的里程")
        t0 = np.datetime64(seg.times[0])
        for i, t in enumerate(seg.times):
            got_h = float((np.datetime64(t) - t0) / np.timedelta64(1, 'h'))
            self.assertLess(abs(got_h - cum[i] / 60.0), 0.05,
                            "第 %d 个点的时刻按 %.1f km 推的，线走到那里只有 %.1f km"
                            % (i, got_h * 60.0, cum[i]))

    def test_pull_never_makes_the_path_worse(self):
        for loader, th in ((_StubLoader(0.0), 1.0), (_WallLoader(), 1.0),
                           (_BandLoader(), 26.0)):
            with self.subTest(tke=loader.tke_value, hard=th):
                grid = self._seg(loader, th, pull=False)
                pulled = self._seg(loader, th, pull=True)
                self.assertEqual(grid.found, pulled.found)
                if not pulled.found:
                    continue
                self.assertLessEqual(pulled.final_g_score, grid.final_g_score + 1e-6,
                                     "拉直只会接受更便宜的直连，总代价不可能变高")
                self.assertTrue(all(t < th for t in pulled.tkes), "拉直穿过了硬阈值")
                self.assertEqual(len(pulled.path), len(pulled.times))
                self.assertEqual(len(pulled.path), len(pulled.tkes))
                self.assertEqual(len(pulled.path), len(pulled.edrs))
                mono = [t for t in pulled.times]
                self.assertEqual(mono, sorted(mono), "时刻必须随里程单调推进")

    def test_legal_but_costlier_shortcut_is_refused(self):
        """直连合法（20 < 26）却要穿过一片中度湍流区，绕一下更便宜 —— 不许拉。

        分辨率用生产的 0.045°：软惩罚在 A* 里是「每格一步收一次」，拿 0.5° 的粗网格
        来测等于把罚量稀释 11 倍，用例就假了。
        """
        res = 0.045
        a, b, mid = (30.0, 90.0), (29.0, 94.0), (31.4, 91.8)
        band = _BandLoader(tke_value=20.0, lat_range=(28.5, 30.6), lon_range=(91.4, 92.2))
        v = [a, mid, b]
        r = self._refs(v, band, 26.0, res=res)
        cost, km, blocked = planner._straight_hop(
            a, b, r["base"], 0.0, 60.0, r["tke"], r["edr"], r["lk"],
            26.0, 0.9, 1.0, 111.0 * res / 2.0, 111.0 * res)
        self.assertFalse(blocked, "直连得是「合法」的，否则测的就不是代价判据")
        self.assertGreater(cost, r["g"][-1] * 1.2, "用例不成立：直连其实更便宜")
        self.assertIsNone(self._pull(v, r, 26.0),
                          "直连合法却更贵 —— 这正是「只判能不能过」会犯的错")

        # 同一条 V 在净空里应该被拉成直线：证明上面的拒绝来自代价，不是判据坏了
        c = self._refs(v, _StubLoader(0.0), 26.0, res=res)
        out = self._pull(v, c, 26.0)
        self.assertIsNotNone(out)
        self.assertLess(max(self._off_line_km(p, a, b) for p in out[0]), 0.05)
        self.assertEqual(out[0][0], a)
        self.assertEqual(out[0][-1], b)
        self.assertLess(out[3], c["d"][-1])


class TestSearchEffortOf(unittest.TestCase):
    """「研究过的路口 / 预算」这对数：整条线按段求和，没跑到搜索时不给。"""

    @staticmethod
    def _plan(segs):
        return PlanResult(path=[], total_distance=0.0, total_grid_cost=0.0,
                          found=False, reason='iteration_limit', segment_results=segs)

    def _seg(self, iters, budget, elapsed=0.0):
        return SegmentResult(path=[], found=False, reason='iteration_limit',
                             iterations=iters, expansion_budget=budget,
                             elapsed_s=elapsed)

    def test_sums_over_segments(self):
        """用时也按段求和并留一位小数：它是「预算付完」与「墙钟先到」的区分依据。"""
        got = planner.search_effort_of(
            self._plan([self._seg(100, 20000, 7.34), self._seg(50, 20000, 2.0)]))
        self.assertEqual(got, {"expansions": 150, "budget": 40000, "elapsed_s": 9.3})

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
