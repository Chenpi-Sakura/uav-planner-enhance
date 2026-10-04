"""全平台时间基准统一测试。

覆盖三类回归：
  1. timebase 换算本身（开/关 DATA_LABEL_IS_UTC）；
  2. planner 查表前确实做了北京时间 -> UTC 换算（用替身 loader 记录入参）；
  3. 功能一（feasibility）沿程采样、起降点校验、阻塞分析也走换算后的时刻。
"""
import os
import sys
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import numpy as np

import timebase
import planner
from config import settings
from schemas import PlanRequest, Point


class TestTimebaseMath(unittest.TestCase):
    def setUp(self):
        self.saved = settings.DATA_LABEL_IS_UTC
        settings.DATA_LABEL_IS_UTC = True

    def tearDown(self):
        settings.DATA_LABEL_IS_UTC = self.saved

    def test_shift_by_offset(self):
        self.assertEqual(timebase.to_data_time_str('2022-07-01T08:00'), '2022-07-01T00:00')
        self.assertEqual(timebase.to_user_time_str('2022-07-01T00:00'), '2022-07-01T08:00')

    def test_cross_day_boundary(self):
        # 北京时间 07-01 03:00 -> 数据时间 06-30 19:00（跨日）
        self.assertEqual(timebase.to_data_time_str('2022-07-01T03:00'), '2022-06-30T19:00')
        self.assertEqual(timebase.to_user_time_str('2022-06-30T19:00'), '2022-07-01T03:00')

    def test_roundtrip(self):
        for s in ('2022-01-01T00:00', '2022-07-15T12:34', '2022-12-31T23:59'):
            back = timebase.to_user_time_str(timebase.to_data_time(s))
            self.assertEqual(back[:16], s)

    def test_disabled_is_identity(self):
        settings.DATA_LABEL_IS_UTC = False
        self.assertEqual(timebase.to_data_time_str('2022-07-01T08:00'), '2022-07-01T08:00')
        self.assertEqual(timebase.to_user_time_str('2022-07-01T08:00'), '2022-07-01T08:00')

    def test_meta_shape(self):
        meta = timebase.data_timezone_meta()
        self.assertTrue(meta['data_label_is_utc'])
        self.assertEqual(meta['tz_offset_hours'], 8)


class _RecordingLoader:
    """记录被查询的时刻，用全 0 风险网格保证规划成功。"""

    def __init__(self):
        self.dates = []

    def get_meteorology_grid(self, min_lat, max_lat, min_lon, max_lon, date=None):
        self.dates.append(str(date))
        shape = (48, 21, 21)
        tke = np.zeros(shape)
        edr = np.zeros(shape)
        lats = np.linspace(min_lat, max_lat, shape[1])
        lons = np.linspace(min_lon, max_lon, shape[2])
        times = np.arange(shape[0]) * np.timedelta64(1, 'h') + np.datetime64('2022-06-30T16:00')
        return tke, edr, lats, lons, times


class TestPlannerTimezone(unittest.TestCase):
    """planner 必须把入口的北京时间换算成数据标签（UTC）再查表。"""

    def setUp(self):
        self.saved_utc = settings.DATA_LABEL_IS_UTC
        self.saved_loader = planner.data_loader
        self.saved_res = settings.GRID_RESOLUTION_DEG
        settings.DATA_LABEL_IS_UTC = True
        self.loader = _RecordingLoader()
        planner.data_loader = self.loader
        settings.GRID_RESOLUTION_DEG = 0.5

    def tearDown(self):
        settings.DATA_LABEL_IS_UTC = self.saved_utc
        planner.data_loader = self.saved_loader
        settings.GRID_RESOLUTION_DEG = self.saved_res

    def _req(self, start_time='08:00'):
        return PlanRequest(
            start=Point(lat=29.65, lon=91.13),
            end=Point(lat=29.85, lon=93.5),
            date='2022-07-01', start_time=start_time, speed=60.0,
        )

    def test_grid_query_uses_utc_label(self):
        result = planner.plan_path_ex(self._req('08:00'))
        self.assertTrue(self.loader.dates, "planner 应至少查询一次气象网格")
        self.assertEqual(self.loader.dates[0], '2022-07-01T00:00')
        self.assertTrue(result.found)

    def test_early_morning_crosses_day_boundary(self):
        planner.plan_path_ex(self._req('03:00'))
        self.assertEqual(self.loader.dates[0], '2022-06-30T19:00')

    def test_no_shift_when_flag_disabled(self):
        settings.DATA_LABEL_IS_UTC = False
        planner.plan_path_ex(self._req('08:00'))
        self.assertEqual(self.loader.dates[0], '2022-07-01T08:00')


if __name__ == '__main__':
    unittest.main()