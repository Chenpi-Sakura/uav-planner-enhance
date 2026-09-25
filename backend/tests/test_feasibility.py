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
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import numpy as np

import feasibility
import geo
from config import settings
from planner import PlanResult
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
        return {"tke": np.full(n, 0.1), "edr": np.full(n, 0.001)}

    def get_meteorology_grid(self, *args, **kwargs):
        return None, None, None, None, None


def _fake_plan(total_distance: float, found: bool = True, reason: str = 'blocked'):
    """构造一条拉萨→林芝的合成航线（20 点），沿程风险恒为低。

    found=False 时 reason 决定规划失败的性质：blocked 真没有路，
    iteration_limit 只是没搜完。
    """
    n = 20
    lats = np.linspace(29.65, 29.66, n)
    lons = np.linspace(91.13, 94.36, n)
    path = [Point(lat=float(a), lon=float(b)) for a, b in zip(lats, lons)]
    times = [np.datetime64('2022-07-01T00:00') + np.timedelta64(i * 900, 's') for i in range(n)]
    return PlanResult(
        path=path if found else [],
        total_distance=total_distance if found else 0.0,
        total_grid_cost=0.0,
        found=found,
        reason=None if found else reason,
        times=times if found else [],
        tkes=[0.1] * n if found else [],
        edrs=[0.001] * n if found else [],
        waypoints_reached=[1] if found else [],
        segment_results=[],
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
        feasibility.plan_path_ex = lambda req: plan
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
        result = self._assess(_fake_plan(0.0, found=False, reason='iteration_limit'))
        self.assertFalse(result.feasible)
        self.assertEqual(result.conclusion, '未算出')
        self.assertEqual(result.verdict, 'undetermined')
        self.assertEqual(result.reason, 'search_iteration_limit')
        self.assertEqual(result.meta['plan_reason'], 'iteration_limit')
        # 建议文案要给出可操作的下一步，而不是只说「放宽时间窗口」
        self.assertIn('没有收敛', result.suggestions['text'])
        self.assertIn('途经点', result.suggestions['text'])

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
    """一次点击同时产出三档评估（合并后的「开始规划」走这条）。"""

    def setUp(self):
        self.saved_plan = feasibility.plan_path_ex
        self.saved_loader = feasibility.data_loader
        self.saved_bases = feasibility._suggest_alt_bases
        self.saved_windows = feasibility._suggest_alt_windows
        feasibility.data_loader = _StubLoader()

    def tearDown(self):
        feasibility.plan_path_ex = self.saved_plan
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
        """三档各给一条不同几何的航线（_fake_plan 的几何固定，靠纬度错开）。"""
        plans = {}
        for tier, km, shift in (('urgent', 340.0, 0.0), ('normal', 360.0, 0.05),
                                ('economy', 400.0, 0.1)):
            plan = _fake_plan(km)
            if shift:
                plan.path = [Point(lat=float(p.lat) + shift, lon=p.lon) for p in plan.path]
            plans[tier] = plan
        feasibility.plan_path_ex = lambda req: plans[req.priority]

    def test_three_tiers_come_back_in_configured_order(self):
        self._split_plans()
        out = feasibility.assess_feasibility_multi(self._req())
        self.assertEqual([r['priority'] for r in out['results']],
                         ['urgent', 'normal', 'economy'])
        self.assertEqual(out['active'], 'normal')
        self.assertEqual(out['meta']['n_feasible'], 3)
        self.assertFalse(out['meta']['all_identical'])
        self.assertEqual(out['meta']['distinct_paths'], 3)
        # 每档带自己的里程，卡片要并排显示 距离/绕行比
        self.assertEqual([r['distance']['route_km'] for r in out['results']],
                         [340.0, 360.0, 400.0])

    def test_identical_geometry_is_reported_not_hidden(self):
        """三档同解时如实标 all_identical，前端据此说明而不是假装有三个方案。"""
        same = _fake_plan(340.0)
        feasibility.plan_path_ex = lambda req: same
        out = feasibility.assess_feasibility_multi(self._req())
        self.assertTrue(out['meta']['all_identical'])
        self.assertEqual(out['meta']['distinct_paths'], 1)

    def test_unknown_priority_falls_back_to_default_tier(self):
        self._split_plans()
        out = feasibility.assess_feasibility_multi(self._req(priority='urgentt'))
        self.assertEqual(out['active'], settings.DEFAULT_PRIORITY)

    def test_only_active_tier_pays_for_alt_suggestions(self):
        """替代起降点/时段服务的是详情面板，三档各算一遍会把取数放大三倍。"""
        feasibility.plan_path_ex = lambda req: _fake_plan(0.0, found=False)
        asked = []
        feasibility._suggest_alt_bases = lambda req, **kw: asked.append(req.priority) or []
        feasibility._suggest_alt_windows = lambda *a, **kw: []
        out = feasibility.assess_feasibility_multi(self._req(priority='economy'))
        self.assertEqual(out['active'], 'economy')
        self.assertEqual(asked, ['economy'], f"只有当前档该算替代点，实际算了 {asked}")


if __name__ == "__main__":
    unittest.main(verbosity=2)