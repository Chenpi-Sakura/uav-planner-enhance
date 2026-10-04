"""
风险分级与风险剖面测试（计划书 §9.1）。

阈值用的是「脉动方差」口径（定档依据见 config.py），数值会随标注重走，
数值会随标注重走，判级逻辑不会。所以这里一律用 settings 的阈值表达边界，
不再写死 0.4 / 0.7 / 1.0 这类字面量。

运行：cd backend; .python/portable/python.exe -m unittest discover -s tests
"""
import os
import sys
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import meteorology
import risk
from config import settings

TKE_MID = settings.TKE_RISK_MODERATE
TKE_HIGH = settings.TKE_RISK_HIGH
TKE_UNFLYABLE = settings.TKE_THRESHOLD_HIGH
EDR_MID = settings.EDR_RISK_MODERATE
EDR_HIGH = settings.EDR_RISK_HIGH
EDR_UNFLYABLE = settings.EDR_THRESHOLD_HIGH


class TestRiskClassification(unittest.TestCase):
    """三档阈值下的边界值测试：命中用 >=，与 planner 的硬约束口径一致。"""

    def test_thresholds_are_ordered(self):
        self.assertLess(TKE_MID, TKE_HIGH)
        self.assertLess(TKE_HIGH, TKE_UNFLYABLE)
        self.assertLess(EDR_MID, EDR_HIGH)
        self.assertLess(EDR_HIGH, EDR_UNFLYABLE)

    def test_edr_bands_match_tke_bands(self):
        """EDR 三档必须由 TKE 三档换算而来，否则两维判据会互相打架。"""
        for k, edr in ((TKE_MID, EDR_MID), (TKE_HIGH, EDR_HIGH), (TKE_UNFLYABLE, EDR_UNFLYABLE)):
            self.assertAlmostEqual(float(meteorology.edr_from_tke(k)), edr, delta=edr * 0.01)

    def test_low_risk(self):
        self.assertEqual(risk.classify_point(TKE_MID * 0.25, EDR_MID * 0.25), risk.LEVEL_LOW)
        self.assertEqual(risk.classify_point(TKE_MID * 0.99, EDR_MID * 0.99), risk.LEVEL_LOW)

    def test_moderate_boundary(self):
        self.assertEqual(risk.classify_point(TKE_MID, 0.0), risk.LEVEL_MODERATE)
        self.assertEqual(risk.classify_point(0.0, EDR_MID), risk.LEVEL_MODERATE)

    def test_high_boundary(self):
        self.assertEqual(risk.classify_point(TKE_HIGH, 0.0), risk.LEVEL_HIGH)
        self.assertEqual(risk.classify_point(0.0, EDR_HIGH), risk.LEVEL_HIGH)

    def test_unflyable_boundary(self):
        self.assertEqual(risk.classify_point(TKE_UNFLYABLE, 0.0), risk.LEVEL_UNFLYABLE)
        self.assertEqual(risk.classify_point(0.0, EDR_UNFLYABLE), risk.LEVEL_UNFLYABLE)

    def test_nan_is_not_flagged(self):
        # NaN 不应被判为高风险/不可飞（NaN 由数据缺失在别处处理）
        self.assertEqual(risk.classify_point(float('nan'), 0.0), risk.LEVEL_LOW)

    def test_two_dimension_takes_worse(self):
        # TKE 低但 EDR 高 -> 取较高等级
        self.assertEqual(risk.classify_point(TKE_MID * 0.5, (EDR_HIGH + EDR_UNFLYABLE) / 2.0),
                         risk.LEVEL_HIGH)


class TestRiskProfile(unittest.TestCase):
    """风险剖面构建与合成。"""

    def _samples(self, tke_list, edr_list, step_km=5.0):
        out = []
        for i, (t, e) in enumerate(zip(tke_list, edr_list)):
            out.append({"lat": 29.0 + i * 0.05, "lon": 91.0, "cum_km": i * step_km, "tke": t, "edr": e})
        return out

    def test_overall_low(self):
        s = self._samples([TKE_MID * 0.1, TKE_MID * 0.3, TKE_MID * 0.5],
                          [EDR_MID * 0.1, EDR_MID * 0.2, EDR_MID * 0.3])
        prof = risk.build_risk_profile(s)
        self.assertEqual(prof.overall, risk.LEVEL_LOW)
        self.assertEqual(prof.stats.n_samples, 3)

    def test_overall_moderate(self):
        s = self._samples([TKE_MID * 0.2, TKE_MID * 1.2, TKE_MID * 0.5],
                          [EDR_MID * 0.1] * 3)
        prof = risk.build_risk_profile(s)
        self.assertEqual(prof.overall, risk.LEVEL_MODERATE)

    def test_overall_unflyable_dominates(self):
        s = self._samples([TKE_MID * 0.2, TKE_UNFLYABLE * 1.5, TKE_MID * 0.5],
                          [EDR_MID * 0.1] * 3)
        prof = risk.build_risk_profile(s)
        self.assertEqual(prof.overall, risk.LEVEL_UNFLYABLE)

    def test_moderate_ratio_promotes(self):
        # 中度样本占比超过阈值时整体上调
        s = self._samples([TKE_MID * 1.1] * 4 + [TKE_MID * 0.2], [EDR_MID * 0.1] * 5)
        prof = risk.build_risk_profile(s)
        self.assertEqual(prof.overall, risk.LEVEL_HIGH)

    def test_max_exposure_computed(self):
        # 连续 3 个中度样本，跨度应为 2 步 = 10km
        s = self._samples([TKE_MID * 0.2, TKE_MID * 1.1, TKE_MID * 1.1, TKE_MID * 1.1, TKE_MID * 0.2],
                          [EDR_MID * 0.1] * 5)
        prof = risk.build_risk_profile(s)
        self.assertAlmostEqual(prof.stats.max_exposure_km, 10.0, places=1)

    def test_worst_index_points_at_peak_and_sample_carries_cum_km(self):
        """地图要标「沿程最危险点」，靠的就是 worst_index + 样本的经纬度/里程。"""
        s = self._samples([TKE_MID * 0.2, TKE_MID * 0.3, TKE_MID * 1.4, TKE_MID * 0.2],
                          [EDR_MID * 0.1] * 4, step_km=10.0)
        prof = risk.build_risk_profile(s)
        self.assertEqual(prof.stats.worst_index, 2)
        worst = prof.samples[2]
        self.assertEqual(worst["i"], 2)
        self.assertEqual(worst["level"], risk.LEVEL_MODERATE)
        self.assertAlmostEqual(worst["cum_km"], 20.0, places=3)
        self.assertEqual(worst["lat"], s[2]["lat"])

    def test_sample_without_cum_km_is_none_not_crash(self):
        """route_options 汇总用的样本没有 cum_km，缺它也不能报错。"""
        s = [{"lat": 29.0, "lon": 91.0, "tke": TKE_MID * 0.2, "edr": 0.01},
             {"lat": 29.1, "lon": 91.0, "tke": TKE_MID * 0.3, "edr": 0.01}]
        prof = risk.build_risk_profile(s)
        self.assertIsNone(prof.samples[0]["cum_km"])

    def test_segments_split(self):
        # 前半段低风险、后半段含中度样本
        mid = round(TKE_MID * 1.5, 4)
        s = self._samples([TKE_MID * 0.2, TKE_MID * 0.4, TKE_MID * 0.4, mid, TKE_MID * 0.2],
                          [EDR_MID * 0.1] * 5)
        bounds = [
            {"from_name": "A", "to_name": "B", "start_index": 0, "end_index": 2},
            {"from_name": "B", "to_name": "C", "start_index": 2, "end_index": 4},
        ]
        prof = risk.build_risk_profile(s, bounds)
        self.assertEqual(len(prof.segments), 2)
        self.assertEqual(prof.segments[0].level, risk.LEVEL_LOW)
        self.assertEqual(prof.segments[1].level, risk.LEVEL_MODERATE)
        # 分段风险取该段内的最高等级
        self.assertEqual(prof.segments[1].max_tke, mid)

    def test_empty_samples(self):
        prof = risk.build_risk_profile([])
        self.assertEqual(prof.overall, risk.LEVEL_LOW)
        self.assertEqual(prof.stats.n_samples, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
