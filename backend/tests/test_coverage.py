"""`coverage_bbox()`：地图上的「数据覆盖框」图层的数据源。

这个框存在的意义只有一个：让用户分得清「框外是没有数据」和「框内是安全」。
所以未加载/读不到时必须返回 None（前端据此跳过图层），绝不能返回一个猜的框——
那会把"没数据"伪装成"有数据且可飞"。
"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_loader import DataLoader  # noqa: E402


class _Var:
    def __init__(self, values):
        self.values = np.asarray(values)


class _Ds:
    def __init__(self, lats, lons):
        self.lat = _Var(lats)
        self.lon = _Var(lons)
        self.time = _Var(np.array(['2022-01-01T00:00', '2022-01-01T01:00'],
                                  dtype='datetime64[ns]'))


class _Boom:
    """读 .values 就抛，模拟数据集已关闭/损坏。"""

    @property
    def values(self):
        raise RuntimeError('closed dataset')


def _loader(lats=None, lons=None):
    dl = DataLoader()
    if lats is not None:
        dl.ds_u = _Ds(lats, lons)
    return dl


class TestCoverageBbox(unittest.TestCase):
    def test_box_uses_cell_edges_not_cell_centers(self):
        """轴极值是最外圈格点中心，框要铺到格点边缘（±半步长）。"""
        dl = _loader([26.0, 27.0, 28.0], [80.0, 85.0, 90.0])
        self.assertEqual(dl.coverage_bbox(), {
            "lat_min": 25.5, "lat_max": 28.5, "lon_min": 77.5, "lon_max": 92.5,
        })

    def test_descending_axes_are_normalized(self):
        """纬度轴降序（南大北小的切片）也要给出 lo/hi 正确的框。"""
        dl = _loader([28.0, 27.0, 26.0], [90.0, 85.0, 80.0])
        b = dl.coverage_bbox()
        self.assertEqual((b["lat_min"], b["lat_max"]), (25.5, 28.5))
        self.assertEqual((b["lon_min"], b["lon_max"]), (77.5, 92.5))

    def test_none_when_data_not_loaded(self):
        self.assertIsNone(_loader().coverage_bbox())

    def test_none_when_axis_read_fails(self):
        dl = DataLoader()
        dl.ds_u = type('Ds', (), {'lat': _Boom(), 'lon': _Boom(), 'time': _Boom()})()
        self.assertIsNone(dl.coverage_bbox())

    def test_axis_bounds_are_read_only_once(self):
        """轴范围进程内只算一次：第二次不再碰数据集（数据集可能被关掉）。"""
        dl = _loader([26.0, 32.0], [80.0, 100.0])
        first = dl.coverage_bbox()
        dl.ds_u = type('Ds', (), {'lat': _Boom(), 'lon': _Boom(), 'time': _Boom()})()
        self.assertEqual(dl.coverage_bbox(), first)


class TestPadEdgeCoordsRemoved(unittest.TestCase):
    """_pad_edge_coords 已随 Mercator 重采样改造删除。

    它的职责（把着色区补到真实数据边缘）现在由「插值常量延拓 + 覆盖边缘裁剪矩形」
    承担，几何口径改由 _mercator_row_latitudes 保证，见 test_overlay_mercator.py。
    """

    def test_helper_is_gone(self):
        import data_loader
        self.assertFalse(hasattr(data_loader, '_pad_edge_coords'))


if __name__ == '__main__':
    unittest.main()
