"""A* 湍流场取数路径的契约（B10 Step A/B）。

`get_meteorology_grid` 从「dask label-sel」换成「read_slab 的 isel 路径 + 场级缓存」，
这是纯性能改动，结论必须逐比特不变；同时越界语义必须保持「空数组」而不是
被 searchsorted 静默钳到数据集边界帧。这里用替身把契约钉死，
真实数据上的新旧一致性核对见文件末尾（默认跳过，需 UAV_REAL_DATA_TESTS=1）。

运行：cd backend; .python/portable/python.exe -m unittest discover -s tests -t .
"""
import os
import sys
import types
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_loader import DataLoader  # noqa: E402

AXIS = {
    "lat": (25.0, 40.0),
    "lon": (72.0, 103.0),
    "time": (np.datetime64('2022-01-01T00:00', 'ns'),
             np.datetime64('2022-12-31T23:00', 'ns')),
}


def _loader(field_shape=(31, 5, 6)):
    """一个只读替身场的 DataLoader。"""
    dl = DataLoader()
    dl._loaded = True
    dl.ds_u = object()
    dl._axis_bounds_cache = {k: tuple(v) for k, v in AXIS.items()}
    calls = []

    def fake_field(start_time, hours, bbox):
        calls.append((str(start_time), int(hours), tuple(bbox)))
        n, la, lo = field_shape
        t0 = np.datetime64(start_time, 'ns')
        times = t0 + np.arange(n) * np.timedelta64(1, 'h')
        tke = np.arange(n * la * lo, dtype=float).reshape(n, la, lo)
        edr = np.sqrt(tke) * 0.0737
        lats = np.linspace(bbox[0], bbox[1], la)
        lons = np.linspace(bbox[2], bbox[3], lo)
        return tke, edr, lats, lons, times

    dl.read_turbulence_field = fake_field
    return dl, calls


class TestGridCache(unittest.TestCase):
    ARGS = (29.0, 31.0, 90.0, 94.0)

    def test_same_key_reads_once(self):
        dl, calls = _loader()
        a = dl.get_meteorology_grid(*self.ARGS, date='2022-07-01T00:00')
        b = dl.get_meteorology_grid(*self.ARGS, date='2022-07-01T00:00')
        self.assertEqual(len(calls), 1, "三档规划是同一个键，第二次不该再读盘")
        self.assertIs(a, b, "缓存命中必须返回同一对象，而不是重算一遍")

    def test_different_key_reads_again(self):
        dl, calls = _loader()
        dl.get_meteorology_grid(*self.ARGS, date='2022-07-01T00:00')
        dl.get_meteorology_grid(*self.ARGS, date='2022-07-02T00:00')
        dl.get_meteorology_grid(29.5, 31.0, 90.0, 94.0, date='2022-07-01T00:00')
        self.assertEqual(len(calls), 3)

    def test_grid_cache_is_bounded(self):
        from config import settings
        dl, calls = _loader()
        for h in range(12):
            dl.get_meteorology_grid(*self.ARGS, date=f'2022-07-{1 + h:02d}T00:00')
        self.assertLessEqual(len(dl._grid_cache), int(settings.TW_GRID_CACHE_SIZE))
        self.assertEqual(len(calls), 12)

    def test_frames_keep_the_legacy_axis_length(self):
        """帧数 24+half+1 是旧 label-slice 的既有行为，不能被这次改动悄悄改掉。"""
        from config import settings
        dl, calls = _loader()
        dl.get_meteorology_grid(*self.ARGS, date='2022-07-01T00:00')
        self.assertEqual(calls[0][1], 24 + int(settings.TURB_WINDOW_HALF_HOURS) + 1)


class TestCoverageRejection(unittest.TestCase):
    """越界必须回「空数组」，绝不能回钳位后的边界数据。"""

    ARGS = (29.0, 31.0, 90.0, 94.0)

    def _dl(self):
        return _loader()

    def test_date_after_dataset_end(self):
        dl, calls = self._dl()
        tke, edr, lats, lons, times = dl.get_meteorology_grid(*self.ARGS, date='2023-05-01T00:00')
        self.assertIsNotNone(tke, "None 会被 planner 判成 blocked，掩盖「数据之外」")
        self.assertEqual(times.size, 0)
        self.assertEqual(len(calls), 0, "越界不该去读盘")

    def test_date_before_dataset_start(self):
        dl, calls = self._dl()
        _, _, _, _, times = dl.get_meteorology_grid(*self.ARGS, date='2021-01-01T00:00')
        self.assertEqual(times.size, 0)
        self.assertEqual(len(calls), 0)

    def test_bbox_outside_axis(self):
        dl, calls = self._dl()
        _, _, lats, lons, times = dl.get_meteorology_grid(50.0, 52.0, 90.0, 94.0,
                                                          date='2022-07-01T00:00')
        self.assertEqual((lats.size, lons.size, times.size), (0, 0, 0))
        self.assertEqual(len(calls), 0)

    def test_partially_overlapping_date_still_reads(self):
        """数据集最后一天：窗口只有一半在范围内，仍然要读（与旧 sel 行为一致）。"""
        dl, calls = self._dl()
        _, _, _, _, times = dl.get_meteorology_grid(*self.ARGS, date='2022-12-31T18:00')
        self.assertEqual(len(calls), 1)
        self.assertGreater(times.size, 0)


class TestSlabCacheBudget(unittest.TestCase):
    def test_byte_budget_evicts_before_item_count(self):
        """一块 8 天扫描的 u/v/w 约 20 MB，条数上限拦不住它 —— 字节上限才拦得住。"""
        from config import settings
        saved = int(settings.TW_SLAB_CACHE_MAX_BYTES)
        settings.TW_SLAB_CACHE_MAX_BYTES = 25 * 1024 * 1024
        try:
            dl = DataLoader()
            dl._slab_bytes = 0
            dl._slab_cache.clear()
            big = np.zeros((213, 81, 145))          # ≈ 20 MB
            for name in ('u', 'v', 'w'):
                dl._cache_slab((name,), (big,))
            self.assertEqual(list(dl._slab_cache.keys()), [('w',)], "应只留最新一块")
            self.assertEqual(dl._slab_bytes, big.nbytes)
        finally:
            settings.TW_SLAB_CACHE_MAX_BYTES = saved

    def test_item_count_budget_still_applies(self):
        from config import settings
        dl = DataLoader()
        dl._slab_bytes = 0
        dl._slab_cache.clear()
        small = np.zeros((13, 5, 5))
        for i in range(30):
            dl._cache_slab((i,), (small,))
        self.assertLessEqual(len(dl._slab_cache), int(settings.TW_SLAB_CACHE_SIZE))
        self.assertEqual(dl._slab_bytes, sum(v[0].nbytes for v in dl._slab_cache.values()))


class TestAxisBounds(unittest.TestCase):
    def test_descending_axis_still_gives_min_max(self):
        dl = DataLoader()
        dl._axis_bounds_cache = None
        dl.ds_u = types.SimpleNamespace(
            lat=types.SimpleNamespace(values=np.array([31.0, 30.0, 29.0])),
            lon=types.SimpleNamespace(values=np.array([90.0, 91.0, 92.0])),
            time=types.SimpleNamespace(
                values=np.array(['2022-07-01T00:00', '2022-07-01T01:00'],
                                dtype='datetime64[ns]')),
        )
        b = dl._axis_bounds()
        self.assertEqual(b['lat'], (29.0, 31.0))
        self.assertEqual(b['lon'], (90.0, 92.0))
        self.assertEqual(b['time'][0], np.datetime64('2022-07-01T00:00', 'ns'))


# ==================== 真实数据上的新旧一致性（默认跳过） ====================

def _legacy_grid(dl, min_lat, max_lat, min_lon, max_lon, date):
    """改动前的实现，逐行照抄：dask + label 化 .sel，两端含的时间切片。"""
    from config import settings as settings_mod
    target_time = np.datetime64(date) if 'T' in date else np.datetime64(f"{date}T08:00:00")
    end_time = target_time + np.timedelta64(24, 'h')
    half = np.timedelta64(int((settings_mod.TURB_WINDOW_HALF_HOURS)), 'h')
    time_slice = slice(target_time - half, end_time + half)
    lat_slice = (slice(max_lat, min_lat) if dl.ds_u.lat[0] > dl.ds_u.lat[-1]
                 else slice(min_lat, max_lat))
    lon_slice = (slice(max_lon, min_lon) if dl.ds_u.lon[0] > dl.ds_u.lon[-1]
                 else slice(min_lon, max_lon))
    u = dl.ds_u.u.sel(time=time_slice).sel(lev=500).sel(lat=lat_slice, lon=lon_slice).values
    v = dl.ds_v.v.sel(time=time_slice).sel(lev=500).sel(lat=lat_slice, lon=lon_slice).values
    w = dl.ds_w.w.sel(time=time_slice).sel(lev=500).sel(lat=lat_slice, lon=lon_slice).values
    lats = dl.ds_u.lat.sel(lat=lat_slice).values
    lons = dl.ds_u.lon.sel(lon=lon_slice).values
    times = dl.ds_u.time.sel(time=time_slice).values
    from meteorology import pulsation_tke, edr_from_tke
    tke = pulsation_tke(u, v, w)
    edr = edr_from_tke(tke)
    keep = np.asarray(times, dtype='datetime64[ns]') >= np.datetime64(target_time, 'ns')
    return tke[keep], edr[keep], lats, lons, times[keep]


@unittest.skipUnless(os.environ.get('UAV_REAL_DATA_TESTS') == '1',
                     "需要真实 NetCDF（约 20 s）：UAV_REAL_DATA_TESTS=1 时才跑")
class TestRealDataEquivalence(unittest.TestCase):
    """新旧路径必须逐比特一致 —— 合并取数路径的前提是「不动结论」。

    只比对 A* 真正查得到的前 25 帧（[date, date+24h]）。后面 6 帧是旧实现
    尾部窗口被截断的产物（date+24h 之后没有帧可凑满 ±6h），新实现给了满窗，
    差是预期的差，且 A* 在 60 km/h 下要飞满 1440 km 才会碰到。
    """

    @classmethod
    def setUpClass(cls):
        from data_loader import data_loader
        cls.dl = data_loader
        cls.dl.load_data()
        if cls.dl.ds_u is None:
            raise unittest.SkipTest("数据文件不存在")

    def _check(self, bbox, date):
        old = _legacy_grid(self.dl, *bbox, date=date)
        self.dl._grid_cache.clear()
        new = self.dl.get_meteorology_grid(*bbox, date=date)
        n = 25
        np.testing.assert_array_equal(new[2][:n], old[2][:n], err_msg='lat 轴不一致')
        np.testing.assert_array_equal(new[3][:n], old[3][:n], err_msg='lon 轴不一致')
        np.testing.assert_array_equal(new[4][:n], old[4][:n], err_msg='时间轴不一致')
        self.assertEqual(old[0][:n].shape, new[0][:n].shape)
        np.testing.assert_array_equal(new[0][:n], old[0][:n], err_msg='TKE 不一致')
        np.testing.assert_array_equal(new[1][:n], old[1][:n], err_msg='EDR 不一致')

    def test_corridor_lhasa_nyingchi(self):
        self._check((27.65, 31.66, 89.13, 96.36), '2022-07-01T00:00')

    def test_corridor_lhasa_shigatse(self):
        self._check((27.27, 31.27, 86.88, 93.13), '2022-08-10T00:00')

    def test_narrow_bbox(self):
        self._check((29.5, 30.0, 91.0, 91.6), '2022-05-07T19:00')


if __name__ == '__main__':
    unittest.main()
