"""
功能一 · 自动化测试（使用标准库 unittest，无需额外依赖）。

运行：
    python -m unittest discover -s backend/tests -v
    # 或
    python -m pytest backend/tests -q   （若已安装 pytest）
"""
import os
import sys
import math
import unittest

# 让测试可以直接 import backend 下的模块
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import numpy as np

import risk
import blockage
import geo
from config import settings


class TestRiskClassification(unittest.TestCase):
    """风险分级边界值测试。"""

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


class TestBlockage(unittest.TestCase):
    """阻塞区连通域与形状特征。"""

    def _grid(self, shape=(60, 80)):
        lats = np.linspace(29.0, 30.0, shape[0])
        lons = np.linspace(90.0, 92.0, shape[1])
        tke = np.full(shape, 0.2)
        edr = np.full(shape, 0.05)
        return tke, edr, lats, lons

    def test_no_danger_returns_empty(self):
        tke, edr, lats, lons = self._grid()
        out = blockage.analyze_blockage(tke, edr, lats, lons, (29.5, 91.0), (29.5, 91.0))
        self.assertEqual(out, [])

    def test_band_shape_detected(self):
        tke, edr, lats, lons = self._grid()
        # 水平贯穿的条带
        tke[25:30, :] = 1.5
        edr[25:30, :] = 0.4
        out = blockage.analyze_blockage(tke, edr, lats, lons, (29.5, 91.0), (29.5, 91.0))
        self.assertGreaterEqual(len(out), 1)
        self.assertEqual(out[0].shape, blockage.SHAPE_BAND)
        self.assertTrue(out[0].crosses_corridor)
        self.assertGreater(out[0].aspect_ratio, 3.0)

    def test_blob_shape_detected(self):
        tke, edr, lats, lons = self._grid()
        # 近方形团块
        tke[25:35, 35:45] = 1.5
        edr[25:35, 35:45] = 0.4
        out = blockage.analyze_blockage(tke, edr, lats, lons, (29.09, 90.1), (29.09, 91.9))
        self.assertGreaterEqual(len(out), 1)
        self.assertEqual(out[0].shape, blockage.SHAPE_BLOB)

    def test_small_cluster_filtered(self):
        tke, edr, lats, lons = self._grid()
        tke[10, 10] = 1.5
        edr[10, 10] = 0.4
        out = blockage.analyze_blockage(tke, edr, lats, lons, (29.5, 91.0), (29.5, 91.0),
                                       min_area_km2=400.0)
        self.assertEqual(out, [])

    def test_top_n_limit(self):
        tke, edr, lats, lons = self._grid((120, 160))
        tke[10:20, 10:20] = 1.5
        edr[10:20, 10:20] = 0.4
        tke[50:60, 50:60] = 1.5
        edr[50:60, 50:60] = 0.4
        tke[90:100, 90:100] = 1.5
        edr[90:100, 90:100] = 0.4
        out = blockage.analyze_blockage(tke, edr, lats, lons, (29.5, 91.0), (29.5, 91.0), top_n=2)
        self.assertLessEqual(len(out), 2)

    def test_polygon_emitted(self):
        tke, edr, lats, lons = self._grid()
        tke[25:35, 35:45] = 1.5
        edr[25:35, 35:45] = 0.4
        out = blockage.analyze_blockage(tke, edr, lats, lons, (29.09, 90.1), (29.09, 91.9))
        self.assertTrue(out and out[0].polygon)
        self.assertGreaterEqual(len(out[0].polygon), 3)

    def test_point_to_segment(self):
        # 点到线段距离：线段沿经度，点在正上方约 111km*0.1
        d = blockage._point_to_segment_km(29.1, 91.0, (29.0, 90.0), (29.0, 92.0))
        self.assertAlmostEqual(d, 11.1, places=0)


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
