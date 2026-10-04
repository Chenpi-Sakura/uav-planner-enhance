"""
功能一 · 可行性评估编排测试（计划书 §9.1）。

覆盖：
  ① 可飞分支（低风险 + 绕行在阈值内）；
  ② 绕行超阈分支（detour_exceeds_threshold），并验证阈值随任务优先级变化；
  ③ 无安全路径分支（blocked → no_safe_path）。
结论四态（可飞 / 不可飞 / 超预算 / 未算出）由 verdict 承载：绕行超阈与搜索
未收敛都不是「飞不了」，各自有独立用例守着，改动时不要把两者压回同一个字。

风险分级 / 阻塞区 / 地理分段的用例已拆分到 test_risk.py、test_blockage.py、
test_geo（见 test_feasibility.py 的 TestGeo 与 TestConfigThresholds）；
这里只做编排层行为契约，全部使用合成数据与替身，不触碰真实 NetCDF。

运行：cd backend; .python/portable/python.exe -m unittest discover -s tests
"""
import os
import sys
import time
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import numpy as np

import feasibility
import geo
import route_options
from config import settings
from data_loader import is_placeholder_sample as _real_is_placeholder_sample
from planner import PlanResult, SegmentResult
from schemas import FeasibilityRequest, Point


class _StubLoader:
    """替身 data_loader：不读文件，端点始终安全、危险区分析返回空。"""

    ds_u = None

    def __init__(self):
        self.point_calls = []      # [(单点纬度, 该点用的时刻字符串), ...]

    def get_meteorology_at_points(self, lats, lons, times=None):
        n = len(lats)
        ts = list(times) if times is not None else [None] * n
        for lat, t in zip(lats, ts):
            self.point_calls.append((round(float(lat), 4), str(t)))
        # u/v/w/tc 必须给真实取值：feasibility 会用它们过一遍占位哨兵判定，
        # 缺键会被 KeyError 吞掉、把「读到低湍流」也压成「未算出」。
        return {"u": np.full(n, 1.0), "v": np.full(n, 1.0), "w": np.full(n, 0.1),
                "tc": np.full(n, 250.0),
                "tke": np.full(n, 0.1), "edr": np.full(n, 0.001)}

    def get_meteorology_grid(self, *args, **kwargs):
        return None, None, None, None, None

    def check_coverage(self, lat, lon, tol=0.25):
        """feasibility._check_coverage 已委托到 data_loader.check_coverage，
        替身上补同一个接口：合成数据一律视作在覆盖范围内。"""
        return True

    def is_placeholder_sample(self, u, v, w, tc):
        """刻意**不**在这里提供这个方法。

        它在 data_loader 里是模块级函数，不是 DataLoader 的实例方法。之前这里
        补了一个同名实例方法，于是替身比真身多一个属性，把
        ``data_loader.is_placeholder_sample(...)`` 当实例方法调用会抛 AttributeError
        这件事完全盖住了 —— 而线上正是这一行把起降点读数判成了「不安全/未算出」。
        真实调用点已在下面那条用真身跑的用例里守住。
        """
        raise AssertionError("不该被调用：占位判定是模块级函数，不挂在 loader 上")


def _fake_plan(total_distance: float, found: bool = True, reason: str = 'blocked',
               segments=None, tke: float = 0.1, lon_to: float = 94.36):
    """构造一条拉萨→林芝方向的合成航线（20 点），沿程风险恒定。

    found=False 时 reason 决定规划失败的性质：blocked 真没有路，
    iteration_limit 只是没搜完。segments 用来注入各段的展开计数。
    tke / lon_to 只给档位单调性复选用：它按【几何】算代价（与生产一致，
    PlanResult.total_distance 本就是同一串顶点的折线长），所以想让几条线在代价上
    分出高低，得让线长与沿程风险同时错开。
    """
    n = 20
    lats = np.linspace(29.65, 29.66, n)
    lons = np.linspace(91.13, lon_to, n)
    path = [Point(lat=float(a), lon=float(b)) for a, b in zip(lats, lons)]
    times = [np.datetime64('2022-07-01T00:00') + np.timedelta64(i * 900, 's') for i in range(n)]
    return PlanResult(
        path=path if found else [],
        total_distance=total_distance if found else 0.0,
        total_grid_cost=0.0,
        found=found,
        reason=None if found else reason,
        times=times if found else [],
        tkes=[tke] * n if found else [],
        edrs=[0.001] * n if found else [],
        waypoints_reached=[1] if found else [],
        segment_results=list(segments or []),
    )


class TestFeasibilityOrchestration(unittest.TestCase):
    def setUp(self):
        self.saved_plan = feasibility.plan_path_ex
        self.saved_loader = feasibility.data_loader
        self.loader = _StubLoader()
        feasibility.data_loader = self.loader

    def tearDown(self):
        feasibility.plan_path_ex = self.saved_plan
        feasibility.data_loader = self.saved_loader

    def _req(self, priority='normal'):
        return FeasibilityRequest(
            start=Point(lat=29.65, lon=91.13),
            end=Point(lat=29.66, lon=94.36),
            date='2022-07-01', start_time='08:00', speed=60.0, priority=priority,
        )

    def _assess(self, plan, priority='normal'):
        feasibility.plan_path_ex = lambda req, **kw: plan
        return feasibility.assess_feasibility(
            self._req(priority), include_alt_bases=False, include_alt_windows=False)

    def test_feasible_branch(self):
        # 直线约 312.6 km；航线 340 km -> 绕行比 1.09，正常优先级可接受
        result = self._assess(_fake_plan(340.0))
        self.assertTrue(result.feasible)
        self.assertEqual(result.conclusion, '可飞')
        self.assertEqual(result.verdict, 'feasible')
        self.assertIsNone(result.reason)
        self.assertEqual(result.risk['overall'], '低风险')
        self.assertEqual(result.distance['applicable'], True)
        self.assertFalse(result.distance['time_cost_h']['exceeded'])

    def test_detour_exceeded_branch(self):
        # 航线 480 km -> 绕行比约 1.54，超过 normal(1.5)
        result = self._assess(_fake_plan(480.0), priority='normal')
        self.assertFalse(result.feasible)
        # 线是有的，只是超预算 —— 结论不能印成「不可飞」
        self.assertEqual(result.conclusion, '超预算')
        self.assertEqual(result.verdict, 'over_budget')
        self.assertEqual(result.reason, 'detour_exceeds_threshold')
        self.assertTrue(result.distance['time_cost_h']['exceeded'])
        # 距离仍可用（有真实航线，只是成本超阈）
        self.assertEqual(result.distance['applicable'], True)

    def test_detour_threshold_follows_priority(self):
        plan = _fake_plan(420.0)  # 绕行比约 1.34
        urgent = self._assess(plan, priority='urgent')     # 上限 1.25 -> 超阈
        normal = self._assess(plan, priority='normal')     # 上限 1.5  -> 通过
        economy = self._assess(plan, priority='economy')   # 上限 2.0  -> 通过
        self.assertFalse(urgent.feasible)
        self.assertEqual(urgent.reason, 'detour_exceeds_threshold')
        self.assertTrue(normal.feasible)
        self.assertTrue(economy.feasible)
        self.assertLess(urgent.detour_limit, normal.detour_limit)
        self.assertLess(normal.detour_limit, economy.detour_limit)

    def test_blocked_branch(self):
        result = self._assess(_fake_plan(0.0, found=False))
        self.assertFalse(result.feasible)
        self.assertEqual(result.conclusion, '不可飞')
        self.assertEqual(result.verdict, 'infeasible')
        self.assertEqual(result.reason, 'no_safe_path')
        # 无航线时距离/时间成本标记为不适用，前端显示「不适用」
        self.assertEqual(result.distance['applicable'], False)
        self.assertIsNone(result.distance['route_km'])
        self.assertIsNone(result.distance['time_cost_h']['extra'])
        self.assertEqual(result.path, [])

    def test_iteration_limit_is_not_reported_as_unflyable(self):
        """A* 没搜完 ≠ 没有安全航线：越保守的档惩罚越大、越容易撞上上限。"""
        seg = SegmentResult(path=[], found=False, reason='iteration_limit',
                            iterations=20065, expansion_budget=20065, elapsed_s=1.2)
        result = self._assess(_fake_plan(0.0, found=False, reason='iteration_limit',
                                         segments=[seg]))
        self.assertFalse(result.feasible)
        self.assertEqual(result.conclusion, '未算出')
        self.assertEqual(result.verdict, 'undetermined')
        self.assertEqual(result.reason, 'search_iteration_limit')
        self.assertEqual(result.meta['plan_reason'], 'iteration_limit')
        # 建议文案要给出可操作的下一步，而不是只说「放宽时间窗口」
        self.assertIn('仍未收敛', result.suggestions['text'])
        self.assertIn('途经点', result.suggestions['text'])
        # 「没算完」要带规模，否则和「真没路」读起来是同一句话
        self.assertEqual(result.search_effort,
                         {"expansions": 20065, "budget": 20065, "elapsed_s": 1.2})
        self.assertIn('20065', result.suggestions['text'])
        self.assertIn('用满', result.suggestions['text'])

    def test_time_limit_is_undetermined_but_worded_differently(self):
        """墙钟先到也是「没算完」，可它对用户说「用满预算」就是假话 —— 文案按停法分开。"""
        seg = SegmentResult(path=[], found=False, reason='time_limit',
                            iterations=41000, expansion_budget=120000, elapsed_s=18.2)
        result = self._assess(_fake_plan(0.0, found=False, reason='time_limit',
                                         segments=[seg]))
        self.assertEqual(result.verdict, 'undetermined')
        self.assertEqual(result.conclusion, '未算出')
        # 对外仍是同一个 reason 码（同一套色与图标），分开的是那句解释
        self.assertEqual(result.reason, 'search_iteration_limit')
        self.assertEqual(result.meta['plan_reason'], 'time_limit')
        self.assertEqual(result.search_effort,
                         {"expansions": 41000, "budget": 120000, "elapsed_s": 18.2})
        text = result.suggestions['text']
        self.assertIn('仍未收敛', text)
        self.assertIn('尚未用满', text)
        self.assertIn('18 秒的时间上限', text)

    def test_search_effort_absent_when_search_never_ran(self):
        """起点自身被封 / 无数据时压根没进搜索，别报一个「0 / 0」出去。"""
        for reason in ('blocked', 'no_data'):
            with self.subTest(reason=reason):
                result = self._assess(_fake_plan(0.0, found=False, reason=reason))
                self.assertIsNone(result.search_effort)

    def test_search_effort_survives_the_response_model(self):
        """字典 → FeasibilityResponse 这一跳不能把它丢掉（同 09-25 的 planned_level 教训）。"""
        from schemas import FeasibilityResponse
        seg = SegmentResult(path=[], found=False, reason='iteration_limit',
                            iterations=13662, expansion_budget=13662)
        result = self._assess(_fake_plan(0.0, found=False, reason='iteration_limit',
                                         segments=[seg]))
        resp = FeasibilityResponse(**feasibility.to_response_dict(result))
        self.assertEqual((resp.search_effort.expansions, resp.search_effort.budget),
                         (13662, 13662))

    def test_alt_base_sweep_honours_the_request_deadline(self):
        """替代起降点是「未算出」的赠品：实测它比主搜索贵一个数量级，余额用完就得收手。"""
        import planner as planner_module
        real = planner_module.plan_path_ex
        seen = []

        def fake_plan(req, **kw):
            seen.append(kw.get("deadline"))
            return _fake_plan(0.0, found=False, reason='blocked')

        planner_module.plan_path_ex = fake_plan
        try:
            stale = time.monotonic() - 1.0
            self.assertEqual(feasibility._suggest_alt_bases(self._req(), deadline=stale), [])
            self.assertEqual(seen, [], "整次请求的余额已经过了，还在一次一次全程重规划")

            live = time.monotonic() + 30.0
            seen.clear()
            feasibility._suggest_alt_bases(self._req(), deadline=live)
            self.assertGreater(len(seen), 1, "这一扫本就是二十多次规划，用例得真的扫起来")
            self.assertEqual(set(seen), {live}, "每个候选看到的必须是同一份余额")
        finally:
            planner_module.plan_path_ex = real

    def test_alt_window_scan_reuses_the_same_request_deadline(self):
        """功能一里的「换时段」建议内部还要跑 Top-K 规划：它必须从同一份余额里扣。

        自己再取一份 ASTAR_REQUEST_TIME_LIMIT_S 等于把这次点击的时间上限翻倍 ——
        实测那条 3128 km 多段用例因此跑到 37.7 s（上限 30 s），补上透传后 31.3 s。
        """
        import time_window
        real_tw = time_window.assess_time_window
        seen = []

        def fake_tw(req, run_planner=True, deadline=None):
            seen.append(deadline)
            return {"recommended": None, "backup_windows": []}

        t0 = time.monotonic()
        time_window.assess_time_window = fake_tw
        feasibility.plan_path_ex = lambda req, **kw: _fake_plan(0.0, found=False,
                                                                reason='blocked')
        try:
            feasibility.assess_feasibility(self._req(), include_alt_bases=False,
                                           include_alt_windows=True)
        finally:
            time_window.assess_time_window = real_tw
        self.assertEqual(len(seen), 1, "备选时段扫描压根没被走到，用例就只是绿的")
        self.assertIsNotNone(seen[0], "它自己另取了一份时间上限")
        # 断言是【同一份余额】而不是"有个数就行"：中途新建一份预算也是合法的浮点数
        self.assertLessEqual(seen[0], t0 + settings.ASTAR_REQUEST_TIME_LIMIT_S + 0.5)
        self.assertGreaterEqual(seen[0], t0)

    def test_user_facing_time_contract(self):
        # meta 回传的是用户输入口径（北京时间），不是换算后的数据标签
        result = self._assess(_fake_plan(340.0))
        self.assertEqual(result.meta['date'], '2022-07-01')
        self.assertEqual(result.meta['start_time'], '08:00')

    def test_endpoint_check_uses_arrival_time_at_the_far_end(self):
        """起点按起飞时刻、终点按航线到达时刻查（4D 口径的单点版）。

        三档航线到达时刻本就不同，共用起飞时刻会让「降落点天气」
        与沿程剖面各说一套。
        """
        self._assess(_fake_plan(340.0))
        by_lat = {lat: t for lat, t in self.loader.point_calls}
        self.assertEqual(by_lat[29.65], '2022-07-01T00:00')   # 数据时间 = 北京 08:00
        self.assertTrue(by_lat[29.66].startswith('2022-07-01T04:45'),
                        f"终点仍用起飞时刻：{by_lat[29.66]}")

    def test_endpoint_check_falls_back_without_a_route(self):
        self._assess(_fake_plan(0.0, found=False))
        by_lat = {lat: t for lat, t in self.loader.point_calls}
        self.assertEqual(by_lat[29.65], by_lat[29.66])


class TestGeo(unittest.TestCase):
    """地理信息与分段。"""

    def test_nearest_city(self):
        nc = geo.nearest_city(29.65, 91.13)
        self.assertEqual(nc["name"], "拉萨")
        self.assertLess(nc["distance_km"], 1.0)

    def test_describe_point(self):
        self.assertIn("拉萨", geo.describe_point(29.65, 91.13))

    def test_cities_on_path(self):
        # 拉萨 -> 日喀则 的直线附近应命中 曲水 / 尼木 等
        path = [{"lat": 29.65, "lon": 91.13 - i * 0.05, "cum_km": i * 5.0} for i in range(50)]
        hits = geo.cities_on_path(path)
        self.assertGreaterEqual(len(hits), 1)

    def test_segment_bounds_structure(self):
        path = [{"lat": 29.65, "lon": 91.13 - i * 0.05, "cum_km": i * 5.0} for i in range(50)]
        bounds = geo.build_segment_bounds(path)
        self.assertGreaterEqual(len(bounds), 1)
        first = bounds[0]
        for key in ("from_name", "to_name", "start_index", "end_index"):
            self.assertIn(key, first)
        # 索引单调递增且覆盖首尾
        self.assertEqual(first["start_index"], 0)
        self.assertEqual(bounds[-1]["end_index"], len(path) - 1)

    def test_segment_bounds_empty(self):
        self.assertEqual(geo.build_segment_bounds([]), [])


class TestConfigThresholds(unittest.TestCase):
    """配置阈值口径。"""

    def test_detour_limits_ordering(self):
        limits = settings.DETOUR_RATIO_LIMIT
        self.assertLess(limits["urgent"], limits["normal"])
        self.assertLess(limits["normal"], limits["economy"])

    def test_risk_threshold_ordering(self):
        self.assertLess(settings.TKE_RISK_MODERATE, settings.TKE_RISK_HIGH)
        self.assertLess(settings.TKE_RISK_HIGH, settings.TKE_THRESHOLD_HIGH)
        self.assertLess(settings.EDR_RISK_MODERATE, settings.EDR_RISK_HIGH)
        self.assertLess(settings.EDR_RISK_HIGH, settings.EDR_THRESHOLD_HIGH)


class TestFeasibilityMulti(unittest.TestCase):
    """一次点击同时产出三档评估（合并后的「开始规划」走这条）。

    三档的线由 route_options 统一规划并仲裁，所以这里的替身挂在
    route_options.plan_path_ex 上，而不是 feasibility 自己那份。
    """

    def setUp(self):
        self.saved_plan = feasibility.plan_path_ex
        self.saved_ro_plan = route_options.plan_path_ex
        self.saved_loader = feasibility.data_loader
        self.saved_bases = feasibility._suggest_alt_bases
        self.saved_windows = feasibility._suggest_alt_windows
        feasibility.data_loader = _StubLoader()

    def tearDown(self):
        feasibility.plan_path_ex = self.saved_plan
        route_options.plan_path_ex = self.saved_ro_plan
        feasibility.data_loader = self.saved_loader
        feasibility._suggest_alt_bases = self.saved_bases
        feasibility._suggest_alt_windows = self.saved_windows

    def _req(self, priority='normal'):
        return FeasibilityRequest(
            start=Point(lat=29.65, lon=91.13),
            end=Point(lat=29.66, lon=94.36),
            date='2022-07-01', start_time='08:00', speed=60.0, priority=priority,
        )

    def _split_plans(self):
        """三档各一条线，且每档在【自己的倍率】下选自己那条才最优。

        越短的线沿程越热：340 km 罚 100、380 km 罚 30、430 km 零罚，于是
        w=0.25 时 365 < 380 < 527，w=1 时 440 > 380 < 430，w=4 时 740 > 500 > 430。
        写成三条"只有里程差别"的线会被单调性复检并成一条，那就不是在测三档各报自己的数了。
        """
        specs = (('urgent', 340.0, 94.65, 11.1), ('normal', 380.0, 95.06, 8.93),
                 ('economy', 430.0, 95.58, 0.1))
        plans = {tier: _fake_plan(km, tke=t, lon_to=lon_to)
                 for tier, km, lon_to, t in specs}
        route_options.plan_path_ex = lambda req, **kw: plans[req.priority]

    def test_three_tiers_come_back_in_configured_order(self):
        self._split_plans()
        out = feasibility.assess_feasibility_multi(self._req())
        self.assertEqual([r['priority'] for r in out['results']],
                         ['urgent', 'normal', 'economy'])
        self.assertEqual(out['active'], 'normal')
        self.assertEqual(out['meta']['n_feasible'], 3)
        self.assertFalse(out['meta']['all_identical'])
        self.assertEqual(out['meta']['distinct_paths'], 3)
        self.assertEqual(out['meta']['tier_swaps'], [])
        # 每档带自己的里程，卡片要并排显示 距离/绕行比
        self.assertEqual([r['distance']['route_km'] for r in out['results']],
                         [340.0, 380.0, 430.0])

    def test_conservative_tier_adopts_the_cheaper_sibling_line(self):
        """安全档交出一条更贵的线时，卡片换成兄弟档那条 —— 不能印成"越保守越危险"。"""
        specs = (('urgent', 340.0, 94.65, 11.1), ('normal', 380.0, 95.06, 0.1),
                 ('economy', 430.0, 95.58, 20.0))
        plans = {tier: _fake_plan(km, tke=t, lon_to=lon_to)
                 for tier, km, lon_to, t in specs}
        route_options.plan_path_ex = lambda req, **kw: plans[req.priority]
        out = feasibility.assess_feasibility_multi(self._req())
        by = {r['priority']: r for r in out['results']}
        self.assertEqual([by[t]['distance']['route_km'] for t in ('urgent', 'normal')],
                         [340.0, 380.0], "另两档在自己倍率下已是最优，不该被换")
        self.assertEqual(by['economy']['distance']['route_km'], 380.0,
                         "w=4 下平衡档那条更便宜，安全档应采用它")
        self.assertEqual([(s['tier'], s['adopted_from']) for s in out['meta']['tier_swaps']],
                         [('economy', 'normal')])

    def test_identical_geometry_is_reported_not_hidden(self):
        """三档同解时如实标 all_identical，前端据此说明而不是假装有三个方案。"""
        same = _fake_plan(340.0)
        route_options.plan_path_ex = lambda req, **kw: same
        out = feasibility.assess_feasibility_multi(self._req())
        self.assertTrue(out['meta']['all_identical'])
        self.assertEqual(out['meta']['distinct_paths'], 1)

    def test_unknown_priority_falls_back_to_default_tier(self):
        self._split_plans()
        out = feasibility.assess_feasibility_multi(self._req(priority='urgentt'))
        self.assertEqual(out['active'], settings.DEFAULT_PRIORITY)

    def test_only_active_tier_pays_for_alt_suggestions(self):
        """替代起降点/时段服务的是详情面板，三档各算一遍会把取数放大三倍。"""
        route_options.plan_path_ex = lambda req, **kw: _fake_plan(0.0, found=False)
        asked = []
        feasibility._suggest_alt_bases = lambda req, **kw: asked.append(req.priority) or []
        feasibility._suggest_alt_windows = lambda *a, **kw: []
        out = feasibility.assess_feasibility_multi(self._req(priority='economy'))
        self.assertEqual(out['active'], 'economy')
        self.assertEqual(asked, ['economy'], f"只有当前档该算替代点，实际算了 {asked}")


class TestEndpointMissingData(unittest.TestCase):
    """起降点取不到数时，**不许**说它安全。

    这是全套 fail-open 修复里最隐蔽的一条：`get_meteorology_at_points` 在 slab
    读不到时返回的是占位常量而不是 NaN，而且它**不抛异常**。占位常量里
    ``tke=0.0``，直接送进 ``tke >= tke_threshold`` 就是 False，于是
    ``safe = True`` —— 一次读盘失败被说成「起降点零湍流、明确安全」。
    """

    def setUp(self):
        self.saved_loader = feasibility.data_loader
        feasibility.data_loader = _StubLoader()

    def tearDown(self):
        feasibility.data_loader = self.saved_loader

    def _req(self):
        return FeasibilityRequest(
            start=Point(lat=29.65, lon=91.13),
            end=Point(lat=29.66, lon=94.36),
            date='2022-07-01', start_time='08:00', speed=60.0,
        )

    # _check_endpoints 收的是 (lat, lon) 元组，不是 Point
    _START = (29.65, 91.13)
    _END = (29.66, 94.36)

    def _check(self):
        return feasibility._check_endpoints(self._START, self._END, self._req())

    def test_placeholder_sample_is_recognised(self):
        """哨兵判定本身：占位常量认得出，真实读数认不出。"""
        from data_loader import _PLACEHOLDER, is_placeholder_sample
        self.assertTrue(is_placeholder_sample(_PLACEHOLDER['u'], _PLACEHOLDER['v'],
                                              _PLACEHOLDER['w'], _PLACEHOLDER['tc']))
        self.assertFalse(is_placeholder_sample(5.1, 5.0, 0.0, 280.0))
        self.assertFalse(is_placeholder_sample(None, None, None, None))

    def test_placeholder_endpoint_is_not_reported_safe(self):
        """整批返回占位常量时，tke/edr 留空（未算出）且 safe 为 False。"""
        from data_loader import _PLACEHOLDER

        class _PlaceholderLoader(_StubLoader):
            def get_meteorology_at_points(self, lats, lons, times=None):
                n = len(lats)
                return {k: np.full(n, v) for k, v in _PLACEHOLDER.items()}

        feasibility.data_loader = _PlaceholderLoader()
        out = self._check()
        for key in ('start', 'end'):
            self.assertFalse(out[key]['safe'],
                             f"{key} 拿到的是占位常量（tke=0.0），不能判成安全")
            self.assertIsNone(out[key]['tke'], f"{key} 的 tke 应留空表示未算出")
            self.assertIsNone(out[key]['edr'], f"{key} 的 edr 应留空表示未算出")

    def test_real_unsafe_endpoint_is_still_reported(self):
        """对照组：真的读到超阈湍流时，照旧判不安全 —— 别把守卫写成一律 False。"""

        class _HotLoader(_StubLoader):
            def get_meteorology_at_points(self, lats, lons, times=None):
                n = len(lats)
                return {"u": np.full(n, 1.0), "v": np.full(n, 1.0),
                        "w": np.full(n, 0.1), "tc": np.full(n, 250.0),
                        "tke": np.full(n, settings.TKE_THRESHOLD_HIGH + 5.0),
                        "edr": np.full(n, 0.001)}

        feasibility.data_loader = _HotLoader()
        out = self._check()
        for key in ('start', 'end'):
            self.assertFalse(out[key]['safe'], f"{key} 真的超阈，应判不安全")
            self.assertIsNotNone(out[key]['tke'], f"{key} 读到了数，tke 不该是 None")

    def test_real_safe_endpoint_is_still_reported_safe(self):
        """对照组：真的读到低湍流时，safe 仍为 True —— 守卫不能把结论全压成未知。"""
        req = self._req()
        out = feasibility._check_endpoints(self._START, self._END, req)
        for key in ('start', 'end'):
            self.assertTrue(out[key]['safe'], f"{key} 实测 tke=0.1，应判安全")

    def test_placeholder_detection_runs_against_the_real_loader(self):
        """**不替身**跑一遍：占位判定是模块级函数，调用点不许把它当实例方法。

        这条守的是一个已经真实发生过的 bug：调用点写成
        ``data_loader.is_placeholder_sample(...)``，而 ``data_loader`` 是
        ``from data_loader import data_loader`` 拿到的**实例**，实例上没有这个属性
        → AttributeError → 被 ``except Exception: pass`` 吞掉 → 每次评估都把起降点
        判成「不安全且读不出数」。上一批用例之所以全绿，是因为替身比真身多一个属性。
        """
        import data_loader as dl_module
        from data_loader import data_loader as real_loader
        from schemas import Point as _Pt

        self.assertFalse(hasattr(real_loader, "is_placeholder_sample"),
                         "占位判定是模块级函数，不应挂在 loader 实例上")
        self.assertTrue(callable(dl_module.is_placeholder_sample))

        saved = real_loader.get_meteorology_at_points
        try:
            real_loader.get_meteorology_at_points = lambda la, lo, ti=None: {
                "u": [1.0], "v": [1.0], "w": [0.1], "tc": [250.0],
                "tke": [0.1], "edr": [0.001]}
            feasibility.data_loader = real_loader
            out = feasibility._check_endpoints(self._START, self._END, self._req())
        finally:
            real_loader.get_meteorology_at_points = saved

        # 真身路径上必须真的判出 safe，而不是 AttributeError 被吞成 safe=False
        for key in ('start', 'end'):
            self.assertTrue(out[key]['safe'],
                            f"{key} 读到了 tke=0.1，真身路径上应判安全；"
                            f"若这里失败，多半是占位判定又被当成了实例方法")
            self.assertEqual(out[key]['tke'], 0.1)


class TestVerdictMapping(unittest.TestCase):
    """reason 码 -> 结论四态的映射。

    ``verdict_of`` 对**没登记**的 reason 一律返回 infeasible。默认值是「不可飞」，
    所以每加一个 reason 码都必须显式登记 —— 漏登记不会报错，只会安静地把
    「没算出来」说成「飞不了」。
    """

    def test_every_planner_reason_is_registered(self):
        from schemas import PLANNER_REASON_VERDICT
        expected = {'blocked', 'no_data', 'iteration_limit', 'time_limit',
                    'turbulence_unavailable'}
        self.assertEqual(set(PLANNER_REASON_VERDICT), expected)

    def test_turbulence_unavailable_is_undetermined_not_infeasible(self):
        """航线搜到了但沿程湍流取不到数：判「未算出」，不能判「不可飞」。"""
        from schemas import (PLANNER_REASON_VERDICT, VERDICT_UNDETERMINED,
                             verdict_of)
        self.assertEqual(
            verdict_of(False, 'turbulence_unavailable', PLANNER_REASON_VERDICT),
            VERDICT_UNDETERMINED)

    def test_truly_blocked_stays_infeasible(self):
        """对照组：确认没把「封死」也一起降级成「未算出」。"""
        from schemas import PLANNER_REASON_VERDICT, VERDICT_INFEASIBLE, verdict_of
        self.assertEqual(verdict_of(False, 'blocked', PLANNER_REASON_VERDICT),
                         VERDICT_INFEASIBLE)


if __name__ == "__main__":
    unittest.main(verbosity=2)