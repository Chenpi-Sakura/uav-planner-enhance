"""
风险分级与风险剖面测试（计划书 §9.1）。

运行：cd backend; .python/portable/python.exe -m unittest discover -s tests
"""
import os
import sys
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import numpy as np
import risk


class TestRiskClassification(unittest.TestCase):
    """风险分级边界值测试（计划书 §4.3 口径：0.4 / 0.7 / 1.0、0.1 / 0.2 / 0.3）。"""

    def test_low_risk(self):
        self.assertEqual(risk.classify_point(0.1, 0.01), risk.LEVEL_LOW)
        self.assertEqual(risk.classify_point(0.39, 0.09), risk.LEVEL_LOW)

    def test_moderate_boundary(self):
        # TKE 达到 0.4 即为中度
        self.assertEqual(risk.classify_point(0.4, 0.0), risk.LEVEL_MODERATE)
        # EDR 达到 0.1 即为中度
        self.assertEqual(risk.classify_point(0.0, 0.1), risk.LEVEL_MODERATE)

    def test_high_boundary(self):
        self.assertEqual(risk.classify_point(0.7, 0.0), risk.LEVEL_HIGH)
        self.assertEqual(risk.classify_point(0.0, 0.2), risk.LEVEL_HIGH)

    def test_unflyable_boundary(self):
        # TKE >= 1.0 命中硬约束
        self.assertEqual(risk.classify_point(1.0, 0.0), risk.LEVEL_UNFLYABLE)
        # EDR >= 0.3 命中硬约束
        self.assertEqual(risk.classify_point(0.0, 0.3), risk.LEVEL_UNFLYABLE)

    def test_nan_is_not_flagged(self):
        # NaN 不应被判为高风险/不可飞（NaN 由数据缺失在别处处理）
        self.assertEqual(risk.classify_point(float('nan'), 0.0), risk.LEVEL_LOW)

    def test_two_dimension_takes_worse(self):
        # TKE 低但 EDR 高 -> 取较高等级
        self.assertEqual(risk.classify_point(0.1, 0.25), risk.LEVEL_HIGH)


class TestRiskProfile(unittest.TestCase):
    """风险剖面构建与合成。"""

    def _samples(self, tke_list, edr_list, step_km=5.0):
        out = []
        for i, (t, e) in enumerate(zip(tke_list, edr_list)):
            out.append({"lat": 29.0 + i * 0.05, "lon": 91.0, "cum_km": i * step_km, "tke": t, "edr": e})
        return out

    def test_overall_low(self):
        s = self._samples([0.1, 0.2, 0.3], [0.01, 0.02, 0.03])
        prof = risk.build_risk_profile(s)
        self.assertEqual(prof.overall, risk.LEVEL_LOW)
        self.assertEqual(prof.stats.n_samples, 3)

    def test_overall_moderate(self):
        s = self._samples([0.1, 0.5, 0.3], [0.01, 0.02, 0.03])
        prof = risk.build_risk_profile(s)
        self.assertEqual(prof.overall, risk.LEVEL_MODERATE)

    def test_overall_unflyable_dominates(self):
        s = self._samples([0.1, 1.5, 0.3], [0.01, 0.02, 0.03])
        prof = risk.build_risk_profile(s)
        self.assertEqual(prof.overall, risk.LEVEL_UNFLYABLE)

    def test_moderate_ratio_promotes(self):
        # 中度样本占比超过阈值时整体上调
        s = self._samples([0.5, 0.5, 0.5, 0.5, 0.1], [0.01]*5)
        prof = risk.build_risk_profile(s)
        self.assertEqual(prof.overall, risk.LEVEL_HIGH)

    def test_max_exposure_computed(self):
        # 连续 3 个中度样本，跨度应为 2 步 = 10km
        s = self._samples([0.1, 0.5, 0.5, 0.5, 0.1], [0.01]*5)
        prof = risk.build_risk_profile(s)
        self.assertAlmostEqual(prof.stats.max_exposure_km, 10.0, places=1)

    def test_segments_split(self):
        # 前半段低风险、后半段含中度样本
        s = self._samples([0.1, 0.2, 0.2, 0.6, 0.1], [0.01]*5)
        bounds = [
            {"from_name": "A", "to_name": "B", "start_index": 0, "end_index": 2},
            {"from_name": "B", "to_name": "C", "start_index": 2, "end_index": 4},
        ]
        prof = risk.build_risk_profile(s, bounds)
        self.assertEqual(len(prof.segments), 2)
        self.assertEqual(prof.segments[0].level, risk.LEVEL_LOW)
        self.assertEqual(prof.segments[1].level, risk.LEVEL_MODERATE)
        # 分段风险取该段内的最高等级
        self.assertEqual(prof.segments[1].max_tke, 0.6)

    def test_empty_samples(self):
        prof = risk.build_risk_profile([])
        self.assertEqual(prof.overall, risk.LEVEL_LOW)
        self.assertEqual(prof.stats.n_samples, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)