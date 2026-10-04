"""`coverage_bbox()`：地图上的「数据覆盖框」图层的数据源。

这个框存在的意义只有一个：让用户分得清「框外是没有数据」和「框内是安全」。
所以未加载/读不到时必须返回 None（前端据此跳过图层），绝不能返回一个猜的框——
那会把"没数据"伪装成"有数据且可飞"。
"""
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

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


class TestEnsureLoaded(unittest.TestCase):
    """``_ensure_loaded()`` 的并发安全与失败退避。

    这组用例守住一条**真实踩过**的坑：``_ensure_loaded()`` 持锁调用
    ``load_data()``，而 ``load_data()`` 自己还要再拿一次同一把锁。普通
    ``threading.Lock`` 不可重入，同线程二次获取直接自死锁 —— 而仓库里绝大多数
    用例都预置 ``_loaded = True`` 走早退分支，恰好永远走不到那条路径。
    所以这里必须**真的**从冷启动跑一遍。
    """

    def _patched(self, always_fail=False):
        """替换 xr.open_dataset，返回 (loader, 调用记录)。"""
        calls = []

        def _fake_open(path, chunks=None):
            calls.append(path)
            if always_fail:
                raise FileNotFoundError(path)
            return _Ds([26.0, 32.0], [80.0, 100.0])

        patcher = mock.patch('data_loader.xr.open_dataset', _fake_open)
        patcher.start()
        self.addCleanup(patcher.stop)
        return DataLoader(), calls

    def test_cold_start_does_not_deadlock(self):
        """_loaded=False 时走完整条加载路径。

        跑在带超时的线程里：若锁不可重入，这里会**挂死**而不是失败，
        整个测试套件就卡住了。超时即判定死锁。
        """
        ld, _ = self._patched()
        result = {}

        def _worker():
            result['ok'] = ld._ensure_loaded()

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=20)
        self.assertFalse(t.is_alive(),
                         "_ensure_loaded 卡死 —— 锁不可重入（_ensure_loaded 持锁调 "
                         "load_data，load_data 又取同一把锁）")
        self.assertTrue(result.get('ok'), "冷启动应加载成功")
        self.assertIsNotNone(ld.ds_u)

    def test_failure_enters_cooldown_instead_of_retrying_every_request(self):
        """加载失败后退避：连续调用不应把 open 反复重试。"""
        ld, calls = self._patched(always_fail=True)
        for _ in range(5):
            self.assertFalse(self._call_with_timeout(ld))
        self.assertLessEqual(len(calls), 6,
                             f"退避期内不应反复 open，实际调了 {len(calls)} 次")
        self.assertGreater(ld._load_failed_at, 0.0)

    def test_repeated_calls_do_not_reopen(self):
        """已加载后再调不应重开 —— 6 个约 102 GB 的文件不能每个请求开一遍。"""
        ld, calls = self._patched()
        for _ in range(3):
            self.assertTrue(self._call_with_timeout(ld))
        self.assertEqual(len(calls), 6, f"应只开 6 次，实际 {len(calls)}")

    @staticmethod
    def _call_with_timeout(ld, seconds=20):
        """在带超时的线程里调 _ensure_loaded，避免死锁把整个套件卡住。"""
        box = {}

        def _worker():
            try:
                box['r'] = ld._ensure_loaded()
            except Exception as e:                      # noqa: BLE001
                box['r'] = False
                box['e'] = e

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=seconds)
        if t.is_alive():
            raise AssertionError("_ensure_loaded 卡死（锁不可重入？）")
        return box.get('r')

    def test_concurrent_cold_start_opens_each_file_once(self):
        """并发冷启动：open 次数应等于文件数，而不是线程数 × 文件数。

        实测修复前 4 线程冷启动把 open_dataset 调了 48 次（应为 6）。
        """
        ld, calls = self._patched()
        barrier = threading.Barrier(4)
        results = []

        def _worker():
            barrier.wait()
            results.append(self._call_with_timeout(ld))

        threads = [threading.Thread(target=_worker, daemon=True) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
            self.assertFalse(t.is_alive(), "线程卡死 —— 锁不可重入或存在竞态")

        self.assertEqual(results, [True] * 4)
        self.assertEqual(len(calls), 6, f"并发冷启动应只开 6 次，实际 {len(calls)}")

    def test_reentrant_lock_is_required(self):
        """_ensure_loaded -> load_data 会二次取锁，锁必须可重入。"""
        ld, _ = self._patched()
        self.assertIsInstance(ld._load_lock, type(threading.RLock()))


if __name__ == '__main__':
    unittest.main()
