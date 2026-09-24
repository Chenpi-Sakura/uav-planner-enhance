"""get_meteorology_at_points 的「逐点到达时刻」口径。

用替身 slab，不读真实 NetCDF。要守住的契约：
  1. 每个点用的是【自己的时刻】—— 旧实现整条线共用起飞时刻那一帧，
     会让剖面折线图与 4D 规划判定对不上（同一点一有一无）；
  2. 时刻可传 np.datetime64 或 ISO 字符串（feasibility 传字符串、main 传 datetime64）；
  3. 参数不匹配 / 取不到 slab / 没有数据文件时整体退回占位常量（老契约不变），
     不得半取半占位地混着返回。
"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_loader import DataLoader    # noqa: E402
from config import settings            # noqa: E402
from meteorology import edr_from_tke   # noqa: E402

HOUR = np.timedelta64(1, 'h')
T0 = np.datetime64('2022-07-01T00:00', 'ns')
N_FRAMES = 40
LATS = np.array([29.0, 30.0, 31.0])
LONS = np.array([90.0, 91.0, 92.0])


def _make_loader():
    """构造一个只读替身 slab 的 DataLoader。

    u[time, lat, lon] = 该帧在时间轴上的全局小时下标（v/w 恒为 0），
    于是「返回的 u 是多少」直接等价于「取到了第几帧」，断言一目了然。
    """
    times = T0 + np.arange(N_FRAMES) * HOUR
    ld = DataLoader()
    ld._loaded = True
    ld.ds_u = object()          # 只要非 None 即走真实取数分支

    def read_slab(var, start_time, hours, bbox):
        i0 = int(np.searchsorted(times.astype('int64'),
                                 np.datetime64(start_time, 'ns').astype('int64')))
        i0 = max(0, min(i0, N_FRAMES - 1))
        idx = np.arange(i0, min(N_FRAMES, i0 + int(hours)))
        zeros = np.zeros((idx.size, LATS.size, LONS.size))
        if var != 'u':
            return zeros, LATS, LONS, times[idx]
        vals = np.stack([float(g) * np.ones((LATS.size, LONS.size)) for g in idx])
        return vals, LATS, LONS, times[idx]

    ld.read_slab = read_slab
    return ld


class TestPerPointArrivalTime(unittest.TestCase):
    def setUp(self):
        self.ld = _make_loader()
        self.half = int(settings.TURB_WINDOW_HALF_HOURS)

    def test_each_point_uses_its_own_time(self):
        out = self.ld.get_meteorology_at_points(
            [29.5, 30.5, 31.5], [90.5, 91.5, 92.5],
            [T0 + h * HOUR for h in (8, 13, 18)])
        np.testing.assert_allclose(out['u'], [8.0, 13.0, 18.0])

    def test_uniform_times_still_match_the_single_frame(self):
        """整条线同一时刻（旧调用方式的等价场景）也必须取到那一帧。"""
        out = self.ld.get_meteorology_at_points(
            [29.5, 30.5], [90.5, 91.5], [T0 + 20 * HOUR, T0 + 20 * HOUR])
        np.testing.assert_allclose(out['u'], [20.0, 20.0])

    def test_iso_string_times_accepted(self):
        out = self.ld.get_meteorology_at_points(
            [30.5], [91.5], ['2022-07-01T08:00:00'])
        self.assertAlmostEqual(float(out['u'][0]), 8.0, places=6)

    def test_tke_uses_the_centered_window_of_each_point(self):
        """u 逐小时 +1 → 窗口内为等差数列，方差有解析解：half(half+1)/3。"""
        out = self.ld.get_meteorology_at_points(
            [29.5, 30.5, 31.5], [90.5, 91.5, 92.5],
            [T0 + h * HOUR for h in (8, 13, 18)])
        expected = 0.5 * self.half * (self.half + 1) / 3.0
        np.testing.assert_allclose(out['tke'], [expected] * 3, rtol=1e-9)
        np.testing.assert_allclose(out['edr'], edr_from_tke(np.array([expected] * 3)))

    def test_mismatched_times_length_falls_back_to_placeholders(self):
        out = self.ld.get_meteorology_at_points([29.5, 30.5], [90.5, 91.5], [T0])
        self.assertTrue(np.all(out['u'] == 5.0), "参数不匹配时不得半取半占位地混着返回")
        self.assertTrue(np.all(out['tke'] == 0.0))


class TestPlaceholderFallback(unittest.TestCase):
    def test_missing_slab_keeps_placeholder_contract(self):
        ld = _make_loader()
        ld.read_slab = lambda *a, **kw: (None, None, None, None)
        out = ld.get_meteorology_at_points([29.5], [90.5], [T0])
        self.assertEqual(float(out['u'][0]), 5.0)
        self.assertEqual(float(out['tke'][0]), 0.0)

    def test_no_dataset_loaded_returns_placeholders(self):
        ld = DataLoader()
        ld._loaded = True          # 跳过 load_data()，只测 ds_u is None 这条分支
        ld.ds_u = None
        out = ld.get_meteorology_at_points([29.5, 30.5], [90.5, 91.5], [T0, T0])
        self.assertEqual(len(out['u']), 2)
        self.assertTrue(np.all(out['u'] == 5.0))


if __name__ == "__main__":
    unittest.main()
