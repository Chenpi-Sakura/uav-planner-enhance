"""三方案航线（紧急 / 平衡 / 安全）的行为契约。

用合成网格注入，不读真实 NetCDF。要守住的三条口径：
  1. 三档只改湍流软惩罚倍率，硬阈值对三档一致（安全底线不由档位决定）；
  2. 风险场真的有差别时三档要出不同的线，且"越愿意绕 -> 越短的风险暴露"；
  3. 沿程湍流都没超过中风险下沿时，三档必然重合，此时要如实报 all_identical，
     不能让前端把同一条线包装成"三个方案"；
  4. 结论四态由 summarize_plan 给出（功能一三档与功能二 Top-K 共用）：
     没搜完（iteration_limit）报「未算出」，不压成「不可飞」。
"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import planner  # noqa: E402
import route_options  # noqa: E402
from schemas import PlanRequest, Point  # noqa: E402

START = Point(lat=29.65, lon=90.0)
END = Point(lat=29.65, lon=93.0)


class _StripeLoader:
    """替身 data_loader：走廊正中放一块高湍流区，绕得开但没到硬阈值。"""

    def __init__(self, block_value: float = 20.0, base_value: float = 1.0):
        self.block_value = block_value
        self.base_value = base_value

    def get_meteorology_grid(self, min_lat, max_lat, min_lon, max_lon, date=None):
        shape = (48, 41, 41)          # 48 小时 × 0.2° 步长
        lats = np.linspace(min_lat, max_lat, shape[1])
        lons = np.linspace(min_lon, max_lon, shape[2])
        times = np.arange(shape[0]) * np.timedelta64(1, 'h') + np.datetime64('2022-07-01T00:00')
        tke = np.full(shape, self.base_value, dtype=float)
        in_block = (np.abs(lats - 29.65) <= 0.6)[:, None] & (np.abs(lons - 91.5) <= 1.0)[None, :]
        tke[:, in_block] = self.block_value
        edr = np.zeros(shape)
        return tke, edr, lats, lons, times


def _request() -> PlanRequest:
    return PlanRequest(start=START, end=END, date='2022-07-01',
                       start_time='08:00', speed=60.0)


class TestTierRiskScale(unittest.TestCase):
    def test_three_tiers_match_config(self):
        self.assertEqual(planner.tier_risk_scale('urgent'),
                         planner.settings.DETOUR_PENALTY_TIER_SCALE['urgent'])
        self.assertEqual(planner.tier_risk_scale('normal'), 1.0)
        self.assertGreater(planner.tier_risk_scale('economy'),
                           planner.tier_risk_scale('normal'))

    def test_unknown_or_missing_priority_falls_back_to_baseline(self):
        """未知档位退回 1.0，不能让拼错的字符串静默改成另一种代价。"""
        for value in (None, '', 'urgentt', 'URGENT_X'):
            self.assertEqual(planner.tier_risk_scale(value), 1.0, value)

    def test_normal_tier_keeps_accepted_baseline(self):
        """平衡档 = 基线系数：既有 M5 验收结果不能因为引入三档而漂移。"""
        self.assertEqual(planner.tier_risk_scale('normal') * planner.settings.DETOUR_PENALTY_TKE_COEF,
                         planner.settings.DETOUR_PENALTY_TKE_COEF)


class TestPlanRouteOptions(unittest.TestCase):
    def setUp(self):
        self._orig_loader = planner.data_loader
        self._orig_resolution = planner.settings.GRID_RESOLUTION_DEG
        planner.settings.GRID_RESOLUTION_DEG = 0.2

    def tearDown(self):
        planner.data_loader = self._orig_loader
        planner.settings.GRID_RESOLUTION_DEG = self._orig_resolution

    def _install(self, block_value, base_value=1.0):
        planner.data_loader = _StripeLoader(block_value, base_value)

    def test_tiers_split_when_risk_varies(self):
        """有风险梯度时：越愿意绕的档位绕得越远、吃到的湍流越少。

        这里的中成块只有一条像样的绕行走法，所以平衡与安全会同解——断言取
        「单调不减 + 至少两档不同」，而不是硬要求三条几何互不相同。
        """
        self._install(block_value=20.0)
        out = route_options.plan_route_options(_request())
        by = {r['priority']: r for r in out['routes']}

        self.assertFalse(out['meta']['all_identical'])
        self.assertGreaterEqual(out['meta']['distinct_paths'], 2)
        self.assertTrue(all(r['feasible'] for r in out['routes']))
        self.assertLessEqual(by['urgent']['total_distance'], by['normal']['total_distance'])
        self.assertLessEqual(by['normal']['total_distance'], by['economy']['total_distance'])
        self.assertGreater(by['urgent']['max_tke'], by['economy']['max_tke'],
                           "安全档应绕开高湍流块，最大 TKE 应低于紧急档")
        self.assertGreater(by['urgent']['high_ratio'], by['economy']['high_ratio'])

    def test_hard_threshold_is_shared_across_tiers(self):
        """硬阈值不随档位放松：三档都得判不可飞。"""
        self._install(block_value=40.0)  # 40 > TKE_THRESHOLD_HIGH=26
        planner.data_loader = _StripeLoader(block_value=40.0, base_value=40.0)
        out = route_options.plan_route_options(_request())
        self.assertEqual(out['meta']['n_feasible'], 0)
        for r in out['routes']:
            self.assertFalse(r['feasible'], r['priority'])
            self.assertEqual(r['path'], [])
            self.assertIsNotNone(r['reason'])

    def test_calm_field_reports_identical_instead_of_fake_choices(self):
        """全程低于中风险下沿 -> 惩罚不参与代价，三档同一条线，且如实标出来。"""
        planner.data_loader = _StripeLoader(block_value=3.0, base_value=2.0)
        out = route_options.plan_route_options(_request())
        self.assertTrue(out['meta']['all_identical'])
        self.assertEqual(out['meta']['distinct_paths'], 1)
        for r in out['routes']:
            self.assertFalse(r['risk_engaged'], r['priority'])

    def test_route_labels_and_order(self):
        planner.data_loader = _StripeLoader(block_value=2.0, base_value=1.0)
        out = route_options.plan_route_options(_request())
        self.assertEqual([r['priority'] for r in out['routes']],
                         ['urgent', 'normal', 'economy'])
        self.assertEqual([r['label'] for r in out['routes']], ['紧急', '平衡', '安全'])

    def test_response_is_serializable_by_schema(self):
        """响应必须能被 PlanMultiResponse 接住——字段漏定义时 pydantic 会静默丢掉。"""
        from schemas import PlanMultiResponse
        planner.data_loader = _StripeLoader(block_value=20.0)
        out = route_options.plan_route_options(_request())
        model = PlanMultiResponse(**out)
        by = {r.priority: r for r in model.routes}
        self.assertIsNotNone(by['urgent'].detour_ratio, "detour_ratio 未声明进模型会被过滤成 None")
        self.assertGreater(by['economy'].detour_ratio, by['urgent'].detour_ratio)
        self.assertEqual(by['urgent'].verdict, 'feasible', "verdict 漏定义会被 pydantic 丢掉")
        self.assertEqual(by['urgent'].conclusion, '可飞')


class TestSummarizePlanVerdict(unittest.TestCase):
    """summarize_plan 是功能一三档与功能二 Top-K 共用的汇总，结论四态从这里出。"""

    def _plan(self, found: bool, reason=None, segments=()):
        from planner import PlanResult
        path = [START, END] if found else []
        return PlanResult(
            path=path, total_distance=300.0 if found else 0.0, total_grid_cost=0.0,
            found=found, reason=None if found else reason,
            times=[np.datetime64('2022-07-01T00:00')] * len(path),
            tkes=[1.0] * len(path), edrs=[0.01] * len(path),
            waypoints_reached=[1] if found else [], segment_results=list(segments),
        )

    def _sum(self, found, reason=None, segments=()):
        return route_options.summarize_plan(self._plan(found, reason, segments), 200.0, 60.0)

    def test_found_is_feasible(self):
        item = self._sum(True)
        self.assertEqual((item['verdict'], item['conclusion']), ('feasible', '可飞'))

    def test_iteration_limit_row_carries_search_effort(self):
        """功能二的候选行要说「没算完」就得同时给出规模，否则与真没路同义。"""
        from planner import SegmentResult
        seg = SegmentResult(path=[], found=False, reason='iteration_limit',
                            iterations=49440, expansion_budget=49440, elapsed_s=17.5)
        item = self._sum(False, 'iteration_limit', segments=[seg])
        self.assertEqual(item['conclusion'], '未算出')
        self.assertEqual(item['search_effort'],
                         {"expansions": 49440, "budget": 49440, "elapsed_s": 17.5})

    def test_time_limit_row_is_also_undetermined(self):
        """墙钟先到与预算顶格同属「没算完」，但 reason 要原样透出，前端按它分开措辞。"""
        item = self._sum(False, 'time_limit')
        self.assertEqual((item['verdict'], item['conclusion']), ('undetermined', '未算出'))
        self.assertEqual(item['reason'], 'time_limit')

    def test_search_effort_none_when_search_never_ran(self):
        self.assertIsNone(self._sum(False, 'blocked')['search_effort'])

    def test_blocked_is_infeasible(self):
        item = self._sum(False, 'blocked')
        self.assertEqual((item['verdict'], item['conclusion']), ('infeasible', '不可飞'))

    def test_iteration_limit_is_not_called_unflyable(self):
        """没搜完 ≠ 没有航线：功能二候选行的风险档那一格要显示「未算出」。"""
        item = self._sum(False, 'iteration_limit')
        self.assertFalse(item['feasible'])
        self.assertEqual((item['verdict'], item['conclusion']), ('undetermined', '未算出'))
        self.assertEqual(item['reason'], 'iteration_limit')


if __name__ == "__main__":
    unittest.main()
