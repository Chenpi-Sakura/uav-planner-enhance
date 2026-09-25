"""覆盖层行几何：PNG 行必须按 Web Mercator 等距，而不是等纬度距。

L.imageOverlay 把 PNG 线性贴到投影平面的 bounds 上；此前行纬度取源网格的等纬度间隔，
热图相对地图整体漂移——跨 25° 纬度时最大约 0.9°（~100 km），上下边缘为 0、视口
中纬度最大，只偏纬度不偏经度（Mercator 的 x 与经度线性，经度向本来就没错）。

修复后：第 j 行纬度 = ym⁻¹(lerp(ym(north), ym(south), j/(rows-1)))，且绘制改用索引
坐标。本文件的集成测试从 DataLoader 出图路径一路量到 PNG 像素（数据全部用桩，
不碰 NetCDF），断言边界落在 Mercator 预测位、而不是等纬度（旧算法）预测位。
"""
import io
import sys
import types
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_loader import (DataLoader, _cell_edge_extents, _merc_to_lat,  # noqa: E402
                         _merc_y, _mercator_row_latitudes)


class TestMercatorRowLatitudes(unittest.TestCase):
    """行纬度的纯几何定义（步 5 的第一条：第 j 行 = 逆 Mercator 插值）。"""

    def test_endpoints_and_descending_order(self):
        rows = _mercator_row_latitudes(20.0, 45.0, 26)
        self.assertAlmostEqual(float(rows[0]), 45.0, places=9)
        self.assertAlmostEqual(float(rows[-1]), 20.0, places=9)
        self.assertTrue(np.all(np.diff(rows) < 0), '第 0 行必须是北端，向下递减')

    def test_rows_equidistant_in_mercator_y(self):
        rows = _mercator_row_latitudes(20.0, 45.0, 26)
        dy = np.diff([_merc_y(float(v)) for v in rows])
        self.assertTrue(np.allclose(dy, dy[0]))

    def test_midrow_is_mercator_midpoint_not_linear(self):
        rows = _mercator_row_latitudes(20.0, 45.0, 25)   # 奇数行：正中那行 t=0.5
        mid = float(rows[rows.size // 2])
        want = _merc_to_lat((_merc_y(20.0) + _merc_y(45.0)) / 2.0)
        self.assertAlmostEqual(mid, want, places=9)
        # Mercator 中点落在 33.39°N，比线性中点 32.5°N 偏北 ~0.9°——这正是旧图
        # 中视口错位量的符号与大小（行等距纬度、像素等距 y 时，内容整体向北漂）
        self.assertGreater(mid, 32.5 + 0.5)

    def test_degenerate_boxes_raise(self):
        for args in ((30.0, 30.0, 10), (35.0, 30.0, 10), (20.0, 45.0, 1),
                     (20.0, 45.0, 0)):
            with self.assertRaises(ValueError):
                _mercator_row_latitudes(*args)

    def test_cell_edge_extents(self):
        lo, hi = _cell_edge_extents(np.arange(25.0, 36.01, 0.5))
        self.assertAlmostEqual(lo, 24.75)
        self.assertAlmostEqual(hi, 36.25)


class _StubMixin:
    """合成数据出图：场 = 纬度本身或阶梯，量 PNG 像素边界。"""

    STEP_LAT = 30.5          # 阶梯场的跳变纬度
    BOX = dict(min_lat=20.0, max_lat=45.0, min_lon=86.0, max_lon=98.0)

    def _axis(self, descending: bool):
        # 0.05° 步长（约 221 个源格点）：阶梯跳变处的抽稀行距缩到 ~9 px，
        # 量「分界像素」才分得清 Mercator 位和等纬度位（两者差 ~51 px）
        a = np.arange(25.0, 36.001, 0.05)   # 含 30.00/30.50/31.00
        return a[::-1] if descending else a

    def _loader(self, lats: np.ndarray, kind: str) -> DataLoader:
        lons = np.arange(86.0, 98.001, 0.05)
        grid = lats[:, None]
        if kind == 'step':
            # 阶梯：南低北高（降序轴时数组方向自然跟着轴走，翻错就会量出反向边界）
            field = np.where(grid >= self.STEP_LAT, 20.0, -30.0) * np.ones(lons.size)
        else:
            field = np.zeros((lats.size, lons.size))
        loader = DataLoader()
        loader.ds_u = types.SimpleNamespace(lat=lats.copy(), lon=lons.copy())

        def fake_slice(path, var, t, lat_slice, lon_slice):
            return field.copy(), lats.copy(), lons.copy()

        def fake_window(path, var, t, lat_slice, lon_slice):
            # 湍流窗口按 ±TURB_WINDOW_HALF_HOURS(6h) 给 13 帧，中心帧 = 08:00
            times = np.array([np.datetime64('2022-07-01T02:00') + np.timedelta64(i, 'h')
                              for i in range(13)])
            return (np.zeros((times.size, lats.size, lons.size)) + field), \
                lats.copy(), lons.copy(), times

        loader._read_overlay_slice = fake_slice
        loader._read_overlay_window = fake_window
        return loader

    def _png_array(self, loader) -> np.ndarray:
        b = self.BOX
        png = loader.get_overlay_image(b['min_lat'], b['max_lat'],
                                       b['min_lon'], b['max_lon'], 'temp',
                                       '2022-07-01T08:00')
        self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
        from PIL import Image
        return np.asarray(Image.open(io.BytesIO(png)).convert('RGBA'))

    @staticmethod
    def _row_of_lat(img_h: int, lat: float, min_lat: float, max_lat: float,
                    mercator: bool) -> float:
        """PNG 第几像素行代表 lat（顶行 = max_lat）。"""
        if mercator:
            t = (_merc_y(max_lat) - _merc_y(lat)) / (_merc_y(max_lat) - _merc_y(min_lat))
        else:
            t = (max_lat - lat) / (max_lat - min_lat)
        return t * (img_h - 1)


class TestPngMercatorGeometry(unittest.TestCase, _StubMixin):
    def _assert_near(self, name, got, want, tol):
        self.assertLessEqual(abs(got - want), tol, f'{name}: got={got} want={want}')

    def test_step_boundary_follows_mercator_rows(self):
        """阶梯场的颜色分界必须出现在 Mercator 预测行，而不是等纬度预测行。"""
        img = self._png_array(self._loader(self._axis(False), 'step'))
        h = img.shape[0]
        rgb = img[:, img.shape[1] // 2, :3].astype(int)
        opaque = img[:, img.shape[1] // 2, 3] > 8
        rows = np.flatnonzero(opaque)
        # 自顶向下：分界 = 最后一个「顶色」行之后的第一个变色行
        top_color = rgb[rows[0]]
        diff = np.abs(rgb - top_color).sum(axis=1) > 30
        boundary = int(np.flatnonzero(diff & opaque)[0])
        b = self.BOX
        want_m = self._row_of_lat(h, self.STEP_LAT, b['min_lat'], b['max_lat'], True)
        want_old = self._row_of_lat(h, self.STEP_LAT, b['min_lat'], b['max_lat'], False)
        # 分界画在跨越阶梯的那个单元内：允许一格行距的散布（~9 px），但必须显著更靠近 Mercator 位
        self.assertLessEqual(abs(boundary - want_m), 15.0,
                             f'boundary={boundary} merc={want_m:.0f} linear={want_old:.0f}')
        self.assertGreaterEqual(abs(boundary - want_old), 25.0,
                                f'boundary={boundary} 仍坐在等纬度位附近')

    def test_coverage_edges_land_on_mercator_rows(self):
        """平顶场：着色区上下边缘 = 覆盖边缘(格点边缘) 的 Mercator 行，量到像素级。"""
        axis = self._axis(False)
        e_lo, e_hi = _cell_edge_extents(axis)
        img = self._png_array(self._loader(axis, 'flat'))
        h, w = img.shape[:2]
        col = img[:, w // 2, 3] > 8
        rows = np.flatnonzero(col)
        b = self.BOX
        top_want = self._row_of_lat(h, e_hi, b['min_lat'], b['max_lat'], True)
        bot_want = self._row_of_lat(h, e_lo, b['min_lat'], b['max_lat'], True)
        self._assert_near('top edge', float(rows[0]), top_want, 3.0)
        self._assert_near('bottom edge', float(rows[-1]), bot_want, 3.0)
        # 旧口径（格点中心极值的等纬度行）应明显更远——证明量的确实是新几何
        top_old = self._row_of_lat(h, float(axis[-1]), b['min_lat'], b['max_lat'], False)
        bot_old = self._row_of_lat(h, float(axis[0]), b['min_lat'], b['max_lat'], False)
        self.assertGreater(abs(rows[0] - top_old), 10.0)
        self.assertGreater(abs(rows[-1] - bot_old), 10.0)
        # 经度向：源覆盖 == 请求框，着色应铺满左右边缘（x 与经度线性，本来就没错）
        alpha_row = img[rows[len(rows) // 2], :, 3] > 8
        cols = np.flatnonzero(alpha_row)
        self.assertLessEqual(cols[0], 2)
        self.assertGreaterEqual(cols[-1], w - 3)

    def test_descending_source_axes_same_geometry(self):
        """降序源轴（数据轴向翻转的回归位）：边缘位置必须与升序完全一致。

        重采样前把轴和场翻成升序、再翻索引——任何一处漏翻，这里的边界就会镜像。
        """
        img_asc = self._png_array(self._loader(self._axis(False), 'flat'))
        img_desc = self._png_array(self._loader(self._axis(True), 'flat'))
        rows_asc = np.flatnonzero(img_asc[:, img_asc.shape[1] // 2, 3] > 8)
        rows_desc = np.flatnonzero(img_desc[:, img_desc.shape[1] // 2, 3] > 8)
        self.assertTrue(np.array_equal(rows_asc, rows_desc),
                        '同一场在升/降序轴上的着色行集合应逐像素一致')

    def test_box_inside_coverage_fills_whole_canvas(self):
        """请求框完全在覆盖内：裁剪矩形应不生效，画布从第 0 行铺到最后一行。"""
        self.BOX = dict(min_lat=28.0, max_lat=33.0, min_lon=88.0, max_lon=95.0)
        img = self._png_array(self._loader(self._axis(False), 'flat'))
        h, w = img.shape[:2]
        self.assertGreater(int(img[0, w // 2, 3]), 8, '顶行应有着色')
        self.assertGreater(int(img[h - 1, w // 2, 3]), 8, '底行应有着色')
        self.assertGreater(int(img[h // 2, 0, 3]), 8, '最左列应有着色')
        self.assertGreater(int(img[h // 2, w - 1, 3]), 8, '最右列应有着色')


class TestTurbLayerStillRenders(unittest.TestCase, _StubMixin):
    def test_tke_step_field_renders(self):
        """湍流层走 ±window 读取路径：改造后仍要能出图且边界同样吃 Mercator 几何。"""
        loader = self._loader(self._axis(False), 'step')
        b = self.BOX
        png = loader.get_overlay_image(b['min_lat'], b['max_lat'], b['min_lon'],
                                       b['max_lon'], 'tke', '2022-07-01T08:00')
        self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
        from PIL import Image
        img = np.asarray(Image.open(io.BytesIO(png)).convert('RGBA'))
        opaque_rows = np.flatnonzero(img[:, img.shape[1] // 2, 3] > 8)
        self.assertGreater(opaque_rows.size, 10)
        h = img.shape[0]
        e_lo, e_hi = _cell_edge_extents(self._axis(False))
        top_want = self._row_of_lat(h, e_hi, b['min_lat'], b['max_lat'], True)
        bot_want = self._row_of_lat(h, e_lo, b['min_lat'], b['max_lat'], True)
        self.assertLessEqual(abs(float(opaque_rows[0]) - top_want), 3.0)
        self.assertLessEqual(abs(float(opaque_rows[-1]) - bot_want), 3.0)


if __name__ == '__main__':
    unittest.main()
