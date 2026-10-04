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

    ``ds_u`` 必须带真的 lat/lon/time 轴：``get_meteorology_at_points`` 现在会先查
    时间轴范围，把越界时刻判成缺测（NaN）。替身缺轴会让这个检查抛异常。
    """
    times = T0 + np.arange(N_FRAMES) * HOUR
    ld = DataLoader()
    ld._loaded = True

    class _FakeAxis:
        def __init__(self, values):
            self.values = np.asarray(values)

    class _FakeDs:
        lat = _FakeAxis(LATS)
        lon = _FakeAxis(LONS)
        time = _FakeAxis(times)

    ld.ds_u = _FakeDs()

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

    def test_mismatched_times_length_falls_back_to_nan(self):
        """参数不匹配 -> 整批 NaN，**不是**占位常量。

        契约变了：这里原本断言回落到 ``{u:5, tke:0}``，但那是「零湍流、零风」，
        等于把一次参数错误说成一次风平浪静。现在统一走 NaN（= 未算出），
        与「slab 读不到」「越界日期」是同一种表达。
        """
        out = self.ld.get_meteorology_at_points([29.5, 30.5], [90.5, 91.5], [T0])
        self.assertTrue(np.all(np.isnan(out['u'])), "参数不匹配时不得半取半占位地混着返回")
        self.assertTrue(np.all(np.isnan(out['tke'])))
        self.assertTrue(np.all(np.isnan(out['edr'])))

    def test_time_before_dataset_start_is_nan_not_the_first_frame(self):
        """数据集起始日**之前**的日期必须判缺测，不能静默钳到首帧。

        ``read_slab`` 用 searchsorted 定位起始帧再 clamp 到 [0, len-1]，
        于是 2022-01-01 之前的日期会取到首帧的真实数值，并标着用户请求的日期
        （``weather_query`` 还会报 available=True 且无 note）。本替身的时间轴
        从 T0=2022-07-01 起，所以 T0 之前 1 小时就是越界点。
        """
        out = self.ld.get_meteorology_at_points([29.5], [90.5], [T0 - 1 * HOUR])
        self.assertTrue(np.isnan(out['tke'][0]),
                        "越界时刻应判缺测(NaN)，而不是返回首帧的湍流值")
        self.assertTrue(np.isnan(out['u'][0]))

    def test_mixed_in_and_out_of_range_keeps_the_good_points(self):
        """一批点里越界与在范围内混着时：越界点 NaN，在范围内的点照常有值。"""
        out = self.ld.get_meteorology_at_points(
            [29.5, 30.5], [90.5, 91.5], [T0 - 3 * HOUR, T0 + 8 * HOUR])
        self.assertTrue(np.isnan(out['tke'][0]))
        self.assertTrue(np.isnan(out['u'][0]))
        self.assertAlmostEqual(float(out['u'][1]), 8.0, places=6)


class TestPlaceholderFallback(unittest.TestCase):
    def test_missing_slab_keeps_placeholder_contract(self):
        """slab 读不到时走占位常量 —— 这是**已知**契约，调用方要用 is_placeholder_sample 挡。

        与上面「参数不匹配 -> NaN」不同：这里是有意保留的兼容路径，
        因为逐点取数的返回值要直接进剖面响应，NaN 会让沿线每个点都成 null、
        图上什么都不剩。所以常量保留，但**必须**由判定函数识别出来。
        """
        ld = _make_loader()
        ld.read_slab = lambda *a, **kw: (None, None, None, None)
        out = ld.get_meteorology_at_points([29.5], [90.5], [T0])
        self.assertEqual(float(out['u'][0]), 5.0)
        self.assertEqual(float(out['tke'][0]), 0.0)
        from data_loader import is_placeholder_sample
        self.assertTrue(is_placeholder_sample(out['u'][0], out['v'][0],
                                              out['w'][0], out['tc'][0]),
                        "占位值必须能被哨兵识别，否则会被读成「零湍流、明确安全」")

    def test_no_dataset_loaded_returns_placeholders(self):
        ld = DataLoader()
        ld._loaded = True          # 跳过 load_data()，只测 ds_u is None 这条分支
        ld.ds_u = None
        out = ld.get_meteorology_at_points([29.5, 30.5], [90.5, 91.5], [T0, T0])
        self.assertEqual(len(out['u']), 2)
        self.assertTrue(np.all(out['u'] == 5.0))


if __name__ == "__main__":
    unittest.main()
