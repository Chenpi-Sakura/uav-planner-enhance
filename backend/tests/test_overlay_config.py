"""覆盖层质量参数：config.json 解析、画布几何与无数据兜底。

前两组是纯函数，不碰 NetCDF，因此在没有数据盘的 CI 里也能跑。
重点是「填错不能炸服务」和「宽高比对齐后像素才没浪费在空白上」。
无数据那条用假造的空白切片跑通出图路径，验证包围框超出覆盖范围时出的是
全透明图而不是 500。
"""
import io
import json
import math
import sys
import tempfile
import types
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from data_loader import DataLoader


def load_overlay(payload):
    """把一段 overlay 配置写进临时 config.json，返回解析后的 settings 字段字典。"""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "config.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        saved = config.CONFIG_FILE
        config.CONFIG_FILE = p
        try:
            return config._load_overlay_config()
        finally:
            config.CONFIG_FILE = saved


class OverlayConfigTest(unittest.TestCase):
    def test_absent_section_keeps_defaults(self):
        self.assertEqual(load_overlay({}), {})
        self.assertEqual(load_overlay({"overlay": None}), {})

    def test_all_knobs_are_parsed(self):
        cfg = load_overlay({"overlay": {
            "dpi": 90, "max_px": 900, "colors": 64, "grid_points": 40,
            "contour_levels": 30, "line_levels": 8, "cache_seconds": 120,
        }})
        self.assertEqual(cfg, {
            "OVERLAY_DPI": 90, "OVERLAY_MAX_PX": 900, "OVERLAY_COLORS": 64,
            "OVERLAY_GRID_POINTS": 40, "OVERLAY_CONTOUR_LEVELS": 30,
            "OVERLAY_LINE_LEVELS": 8, "OVERLAY_CACHE_SECONDS": 120,
        })

    def test_junk_values_are_dropped_not_fatal(self):
        cfg = load_overlay({"overlay": {"dpi": "abc", "colors": None, "line_levels": []}})
        self.assertNotIn("OVERLAY_DPI", cfg)
        self.assertNotIn("OVERLAY_COLORS", cfg)
        self.assertNotIn("OVERLAY_LINE_LEVELS", cfg)

    def test_values_are_clamped_to_renderable_range(self):
        cfg = load_overlay({"overlay": {"dpi": 1, "max_px": 10, "grid_points": 0,
                                        "contour_levels": 1, "line_levels": -5,
                                        "cache_seconds": -60}})
        self.assertEqual(cfg["OVERLAY_DPI"], 30)
        self.assertEqual(cfg["OVERLAY_MAX_PX"], 200)
        self.assertEqual(cfg["OVERLAY_GRID_POINTS"], 8)
        self.assertEqual(cfg["OVERLAY_CONTOUR_LEVELS"], 2)
        self.assertEqual(cfg["OVERLAY_LINE_LEVELS"], 0)
        self.assertEqual(cfg["OVERLAY_CACHE_SECONDS"], 0)

    def test_colors_round_up_to_palette_size(self):
        # 调色板只有 2 的幂有意义；<2 视为关闭量化（RGBA 直出）
        cases = {0: 0, 1: 0, 2: 2, 3: 4, 100: 128, 200: 256, 999: 256}
        for given, want in cases.items():
            cfg = load_overlay({"overlay": {"colors": given}})
            self.assertEqual(cfg["OVERLAY_COLORS"], want, f"colors={given}")


class OverlayCanvasTest(unittest.TestCase):
    def test_wide_bbox_gets_wide_canvas_not_square(self):
        # 约 4.9° x 11.3° 的西藏视野框，Mercator 宽高比 ~2:1
        (fw, fh), (w_px, h_px) = DataLoader._overlay_canvas(27.13, 32.0, 85.45, 96.70, 150, 1600)
        self.assertGreater(w_px / h_px, 1.5)
        self.assertEqual(w_px, 1600)
        self.assertAlmostEqual(fw * 150, w_px, delta=1)
        self.assertAlmostEqual(fh * 150, h_px, delta=1)

    def test_equator_square_bbox_is_square(self):
        # 赤道上 1°x1° 在 Mercator 里才是正方形；纬度越高 y 向被 1/cos(phi) 拉长
        (_, _), (w0, h0) = DataLoader._overlay_canvas(-0.5, 0.5, 90.0, 91.0, 100, 800)
        self.assertAlmostEqual(w0 / h0, 1.0, delta=0.01)
        (_, _), (w28, h28) = DataLoader._overlay_canvas(27.5, 28.5, 90.0, 91.0, 100, 800)
        self.assertLess(w28 / h28, 0.9)
        self.assertAlmostEqual(w28 / h28, math.cos(math.radians(28.0)), delta=0.03)

    def test_degenerate_and_extreme_boxes_never_crash(self):
        for args in ((30.0, 30.0, 90.0, 90.0), (30.0, 31.0, 90.0, 90.0),
                     (-85.0, 85.0, 0.0, 359.0), (30.0, 29.0, 91.0, 90.0)):
            (fw, fh), (w_px, h_px) = DataLoader._overlay_canvas(*args, 100, 1000)
            self.assertGreater(fw, 0)
            self.assertGreater(fh, 0)
            self.assertGreaterEqual(w_px, 1)
            self.assertGreaterEqual(h_px, 1)
            self.assertLessEqual(max(w_px, h_px), 1000)

    def test_height_is_bounded_by_long_edge(self):
        # 竖长视野框：长边落在高度上，宽度按比例收窄
        (_, _), (w_px, h_px) = DataLoader._overlay_canvas(20.0, 40.0, 90.0, 92.0, 150, 1200)
        self.assertEqual(h_px, 1200)
        self.assertLess(w_px, h_px)


class OverlayNoDataTest(unittest.TestCase):
    """包围框整体在数据覆盖范围外：sel() 给的是空数组而不是 None。

    空数组会一路传到 contourf 才炸，接口表现为 500；前端量化后的包围框比视口
    更大，低缩放级别下更容易越界，所以这里必须在出图前判成「无数据」。
    """

    def _loader(self, empty: bool) -> DataLoader:
        loader = DataLoader()
        # 只需要「纬度递减/递增」这个方向判断，不需要真数据
        loader.ds_u = types.SimpleNamespace(
            lat=np.array([40.0, 30.0, 25.0]), lon=np.array([70.0, 90.0, 115.0]))
        if empty:
            loader._read_overlay_slice = lambda *a, **k: (np.zeros((0, 0)), np.zeros((0,)), np.zeros((0,)))
        return loader

    def test_out_of_coverage_bbox_returns_png_not_exception(self):
        loader = self._loader(empty=True)
        png = loader.get_overlay_image(12.0, 24.0, 86.0, 98.0, 'tke', '2022-07-01T08:00')
        self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
        self.assertLess(len(png), 40_000, "无数据时应是近乎空白的图")

    def test_out_of_coverage_bbox_draws_no_fake_contours(self):
        from PIL import Image
        png = self._loader(empty=True).get_overlay_image(12.0, 24.0, 86.0, 98.0, 'tke', '2022-07-01T08:00')
        img = Image.open(io.BytesIO(png)).convert('RGBA')
        self.assertEqual(img.getchannel('A').getextrema(), (0, 0), "无数据时不该有任何不透明像素")

    def test_partial_coverage_still_renders(self):
        # 只有一半在覆盖范围内：切片非空，正常出图，不走兜底
        loader = self._loader(empty=False)
        called = []

        def fake(path, var, t, lat_slice, lon_slice):
            called.append(var)
            lats = np.arange(25.0, 36.0, 0.5)
            lons = np.arange(86.0, 98.0, 0.5)
            return np.zeros((lats.size, lons.size)), lats, lons

        loader._read_overlay_slice = fake
        png = loader.get_overlay_image(24.0, 36.0, 86.0, 98.0, 'tke', '2022-07-01T08:00')
        self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
        self.assertGreater(len(called), 2)


if __name__ == "__main__":
    unittest.main()
