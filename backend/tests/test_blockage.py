"""
阻塞区连通域与形状特征测试（计划书 §9.1）。

运行：cd backend; .python/portable/python.exe -m unittest discover -s tests
"""
import os
import sys
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import numpy as np
import blockage


class TestBlockage(unittest.TestCase):
    """阻塞区连通域与形状特征（计划书 §4.5）。"""

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


if __name__ == "__main__":
    unittest.main(verbosity=2)