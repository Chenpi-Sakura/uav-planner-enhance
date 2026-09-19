"""planner 的 found / 失败原因分支测试。

计划 §9.1 要求验证「plan_path 的 found=True/False 区分（含封死场景）」。
这里用合成的气象网格注入，避免读取 110GB 真实数据；重点不是 A* 的
寻路细节，而是**失败时不得伪装成直线路径**这一行为契约。
"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import planner  # noqa: E402
from planner import PlanResult, SegmentResult  # noqa: E402


def _make_grid(value: float, shape=(5, 5, 5)):
    """构造一个恒定的 TKE/EDR 网格（时间 × 纬 × 经）。"""
    arr = np.full(shape, value, dtype=float)
    return arr


class _StubLoader:
    """替身 data_loader：返回预设网格，避免触碰真实 NetCDF。"""

    def __init__(self, tke_value: float, edr_value: float = 0.0):
        self.tke_value = tke_value
        self.edr_value = edr_value

    def get_meteorology_grid(self, min_lat, max_lat, min_lon, max_lon, date=None):
        shape = (48, 21, 21)  # 48 小时 × 0.1° 网格
        tke = _make_grid(self.tke_value, shape)
        edr = _make_grid(self.edr_value, shape)
        lats = np.linspace(min_lat, max_lat, shape[1])
        lons = np.linspace(min_lon, max_lon, shape[2])
        times = np.arange(shape[0]) * np.timedelta64(1, 'h') + np.datetime64('2022-07-01T00:00')
        return tke, edr, lats, lons, times


class TestPlannerFoundFlag(unittest.TestCase):
    def setUp(self):
        self._orig_loader = getattr(planner, "data_loader", None)
        self._orig_resolution = planner.settings.GRID_RESOLUTION_DEG

    def tearDown(self):
        if self._orig_loader is not None:
            planner.data_loader = self._orig_loader
        planner.settings.GRID_RESOLUTION_DEG = self._orig_resolution

    def _install(self, tke_value, edr_value=0.0, resolution=0.5):
        planner.data_loader = _StubLoader(tke_value, edr_value)
        planner.settings.GRID_RESOLUTION_DEG = resolution

    def test_segment_blocked_when_start_unsafe(self):
        """起点本身在硬阈值内 → found=False，且不得返回直线路径。"""
        self._install(tke_value=5.0)  # 远超 TKE 硬阈值 1.0
        seg = planner.plan_path_segment(
            (29.65, 91.13), (29.85, 94.36),
            tke_thresh=1.0, edr_thresh=0.3,
            start_time_str="2022-07-01T08:00",
            initial_g_score=0.0, initial_dist=0.0, speed_kmh=60.0,
        )
        self.assertIsInstance(seg, SegmentResult)
        self.assertFalse(seg.found)
        self.assertEqual(seg.path, [])
        self.assertEqual(seg.reason, 'blocked')

    def test_segment_blocked_by_edr(self):
        """EDR 命中硬约束同样判为 blocked。"""
        self._install(tke_value=0.0, edr_value=0.9)
        seg = planner.plan_path_segment(
            (29.65, 91.13), (29.85, 94.36),
            tke_thresh=1.0, edr_thresh=0.3,
            start_time_str="2022-07-01T08:00",
            initial_g_score=0.0, initial_dist=0.0, speed_kmh=60.0,
        )
        self.assertFalse(seg.found)

    def test_segment_found_in_clear_air(self):
        """气象条件良好时应能规划出路径，且 found=True。"""
        self._install(tke_value=0.0)
        seg = planner.plan_path_segment(
            (29.65, 91.13), (29.85, 93.5),
            tke_thresh=1.0, edr_thresh=0.3,
            start_time_str="2022-07-01T08:00",
            initial_g_score=0.0, initial_dist=0.0, speed_kmh=60.0,
        )
        self.assertTrue(seg.found)
        self.assertGreater(len(seg.path), 0)
        self.assertIsNone(seg.reason)

    def test_failed_segment_has_no_placeholder_straight_line(self):
        """失败时 path 必须为空——这是「不再静默回退直线」的核心契约。"""
        self._install(tke_value=9.0)
        seg = planner.plan_path_segment(
            (29.65, 91.13), (29.85, 94.36),
            tke_thresh=1.0, edr_thresh=0.3,
            start_time_str="2022-07-01T08:00",
            initial_g_score=0.0, initial_dist=0.0, speed_kmh=60.0,
        )
        self.assertFalse(seg.found)
        self.assertEqual(len(seg.path), 0, "失败路径不得用直线兜底填充")


class TestPlanResultDefaults(unittest.TestCase):
    def test_plan_result_failure_shape(self):
        """PlanResult 在失败时应带 found=False 与原因，供上层判定。"""
        res = PlanResult(
            path=[], total_distance=0.0, total_grid_cost=0.0,
            found=False, reason='blocked', times=[], tkes=[], edrs=[],
            waypoints_reached=[], segment_results=[],
        )
        self.assertFalse(res.found)
        self.assertEqual(res.reason, 'blocked')
        self.assertEqual(res.path, [])

    def test_plan_result_success_shape(self):
        res = PlanResult(
            path=[(29.65, 91.13), (29.85, 94.36)], total_distance=300.0,
            total_grid_cost=1.0, found=True, reason=None,
            times=[], tkes=[0.1, 0.2], edrs=[0.0, 0.0],
            waypoints_reached=[1], segment_results=[],
        )
        self.assertTrue(res.found)
        self.assertIsNone(res.reason)
        self.assertEqual(len(res.path), 2)


if __name__ == "__main__":
    unittest.main()
