"""field= 注入：A* 复用外部已加载的气象切片，越界时回退自读。

功能二 Top-K 给每个候选起飞时刻各规划一条真实航线。若每次都回盘读 31 帧，
一次时间窗口评估要读四份高度重叠的网格；注入扫描场后应为零次读盘。
这里钉三件事：
  ① 注入同一份网格 → 结果与自读逐点全等（口径没被换掉）；
  ② 注入 → 一次 get_meteorology_grid 都不发生；
  ③ 航线探出注入场 → 回退自读，不把「场不够大」说成「不可飞」。
"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import planner  # noqa: E402
from planner import _GridIndex, plan_path_ex, plan_path_segment  # noqa: E402
from schemas import PlanRequest, Point  # noqa: E402

T0 = np.datetime64('2022-06-30T16:00')     # = 北京时间 2022-07-01T00:00
N_HOURS = 48


def _grid(value, lats, lons, n_hours=N_HOURS):
    tke = np.full((n_hours, len(lats), len(lons)), value, dtype=float)
    edr = np.full_like(tke, 0.0)
    times = T0 + np.arange(n_hours) * np.timedelta64('1', 'h')
    return tke, edr, np.asarray(lats), np.asarray(lons), times


def _bbox_of(start, end, pad=None):
    pad = planner.SEGMENT_PAD_DEG if pad is None else pad
    return (min(start[0], end[0]) - pad, max(start[0], end[0]) + pad,
            min(start[1], end[1]) - pad, max(start[1], end[1]) + pad)


class _CountingLoader:
    """自读路径的替身：记录调用次数，返回该 bbox 上的均匀网格。"""

    def __init__(self, tke_value=0.0, empty=False):
        self.tke_value = tke_value
        self.empty = empty
        self.calls = []

    def get_meteorology_grid(self, min_lat, max_lat, min_lon, max_lon, date=None):
        self.calls.append((min_lat, max_lat, min_lon, max_lon, str(date)))
        if self.empty:
            e = np.zeros(0)
            return (np.zeros((0, 0, 0)), np.zeros((0, 0, 0)), e, e, e)
        lats = np.linspace(min_lat, max_lat, 41)
        lons = np.linspace(min_lon, max_lon, 41)
        return _grid(self.tke_value, lats, lons)


class _Base(unittest.TestCase):
    def setUp(self):
        self._orig_loader = planner.data_loader
        self._orig_res = planner.settings.GRID_RESOLUTION_DEG
        planner.settings.GRID_RESOLUTION_DEG = 0.1
        self.loader = _CountingLoader()
        planner.data_loader = self.loader

    def tearDown(self):
        planner.data_loader = self._orig_loader
        planner.settings.GRID_RESOLUTION_DEG = self._orig_res

    def _req(self, start=(29.65, 91.13), end=(29.85, 91.63)):
        return PlanRequest(start=Point(lat=start[0], lon=start[1]),
                           end=Point(lat=end[0], lon=end[1]),
                           date='2022-07-01', start_time='08:00', speed=60.0)


class TestGridIndexStrict(_Base):
    """注入场的下标定位：越界返回 -1，而不是钳到边界格点冒充真实读数。"""

    def _idx(self, strict):
        lats = np.linspace(29.0, 29.4, 5)          # 步距 0.1°
        lons = np.linspace(91.0, 91.4, 5)
        times = T0 + np.arange(4) * np.timedelta64('1', 'h')
        return _GridIndex(lats, lons, times, strict=strict)

    def test_in_range_matches_nonstrict(self):
        s, n = self._idx(True), self._idx(False)
        for t in (T0, T0 + np.timedelta64('2', 'h'), T0 + np.timedelta64('90', 'm')):
            ns = int(np.datetime64(t, 'ns').astype('int64'))
            self.assertEqual(s.time(ns), n.time(ns))
        for v in (29.0, 29.25, 29.4):
            self.assertEqual(s.lat(v), n.lat(v))
        for v in (91.0, 91.1, 91.4):
            self.assertEqual(s.lon(v), n.lon(v))
        self.assertFalse(s.outside)

    def test_out_of_range_time_is_rejected(self):
        idx = self._idx(True)
        before = int((T0 - np.timedelta64('1', 'h')).astype('int64'))
        self.assertEqual(idx.time(before), -1)
        self.assertTrue(idx.outside)

    def test_half_step_tolerance_on_space(self):
        """落在两端格点半格距之内仍算覆盖内（A* 节点量化到 0.01°，浮点差最后一位）。

        轴为 29.0…29.4、步距 0.1°，所以容差边界在 28.95 / 29.45。
        """
        idx = self._idx(True)
        self.assertGreaterEqual(idx.lat(29.0 + 1e-9), 0)
        self.assertGreaterEqual(idx.lat(29.45), 0)
        self.assertFalse(idx.outside)
        self.assertEqual(idx.lat(29.46), -1)
        self.assertEqual(idx.lat(28.9), -1)
        self.assertEqual(idx.lon(90.9), -1)
        self.assertTrue(idx.outside)

    def test_nonstrict_still_clamps(self):
        """自读路径的钳位语义不动 —— 现有黄金用例依赖它。"""
        idx = self._idx(False)
        self.assertEqual(idx.time(int((T0 - np.timedelta64('9', 'h')).astype('int64'))), 0)
        self.assertEqual(idx.lat(20.0), 0)
        self.assertEqual(idx.lon(120.0), 4)
        self.assertFalse(idx.outside)


class TestInjectionEquivalence(_Base):
    def test_injected_grid_equals_self_read_and_reads_nothing(self):
        start, end = (29.65, 91.13), (29.85, 91.63)
        bbox = _bbox_of(start, end)
        lats = np.linspace(bbox[0], bbox[1], 41)
        lons = np.linspace(bbox[2], bbox[3], 41)
        field = _grid(0.0, lats, lons)

        ref = plan_path_ex(self._req(start, end))
        self.assertTrue(ref.found, "对照用例本身要能规划成功")
        self.assertEqual(len(self.loader.calls), 1)

        self.loader.calls.clear()
        got = plan_path_ex(self._req(start, end), field=field)
        self.assertEqual(self.loader.calls, [], "注入后不得再回盘读取")
        self.assertTrue(got.found)
        self.assertEqual([(p.lat, p.lon) for p in got.path],
                         [(p.lat, p.lon) for p in ref.path])
        self.assertEqual(got.tkes, ref.tkes)
        self.assertAlmostEqual(got.total_distance, ref.total_distance, places=9)

    def test_segment_level_injection(self):
        """单段接口同样支持注入，且不再有隐藏的整段读盘。"""
        start, end = (29.65, 91.13), (29.85, 91.63)
        bbox = _bbox_of(start, end)
        field = _grid(0.0, np.linspace(bbox[0], bbox[1], 41),
                      np.linspace(bbox[2], bbox[3], 41))
        self.loader.calls.clear()
        seg = plan_path_segment(start, end, 26.0, 0.376, '2022-07-01T00:00',
                                0.0, 0.0, 60.0, field=field)
        self.assertTrue(seg.found)
        self.assertFalse(seg.escaped)
        self.assertEqual(self.loader.calls, [])


class TestFieldEscapeFallback(_Base):
    def test_narrow_field_falls_back_to_self_read(self):
        """注入场只盖住起点附近 → 搜索探出边界 → 回退自读并给出可行结论。"""
        start, end = (29.65, 91.13), (29.85, 91.63)
        narrow = _grid(0.0, np.linspace(29.60, 29.70, 6), np.linspace(91.10, 91.20, 6))

        seg = plan_path_segment(start, end, 26.0, 0.376, '2022-07-01T00:00',
                                0.0, 0.0, 60.0, field=narrow)
        self.assertTrue(seg.escaped, "探出注入场必须被标记出来")

        self.loader.calls.clear()
        got = plan_path_ex(self._req(start, end), field=narrow)
        self.assertGreaterEqual(len(self.loader.calls), 1, "应回退到自读重算")
        self.assertTrue(got.found)
        self.assertNotEqual(got.reason, planner.REASON_FIELD_ESCAPE)

    def test_escape_flag_surfaces_as_reason_from_seq(self):
        """_plan_seq 单次执行把 escaped 映射成 field_escape，供上层判别。"""
        start, end = (29.65, 91.13), (29.85, 91.63)
        narrow = _grid(0.0, np.linspace(29.60, 29.70, 6), np.linspace(91.10, 91.20, 6))
        res = planner._plan_seq(self._req(start, end), field=narrow)
        self.assertFalse(res.found)
        self.assertEqual(res.reason, planner.REASON_FIELD_ESCAPE)

    def test_empty_field_does_not_leak_escape_reason(self):
        """空注入场也回退；回退后仍无数据时报 no_data，而不是内部原因。"""
        self.loader = _CountingLoader(empty=True)
        planner.data_loader = self.loader
        empty = (np.zeros((0, 0, 0)), np.zeros((0, 0, 0)),
                 np.zeros(0), np.zeros(0), np.zeros(0))
        res = plan_path_ex(self._req(), field=empty)
        self.assertFalse(res.found)
        self.assertEqual(res.reason, 'no_data')

    def test_no_field_never_escapes(self):
        res = plan_path_ex(self._req())
        self.assertTrue(res.found)
        self.assertIsNone(res.reason)
        self.assertEqual(len(self.loader.calls), 1)


if __name__ == "__main__":
    unittest.main()
