"""默认演示日 `resolve_default_date()` 的契约。

替身时间轴，不读真实 NetCDF。要守住的三件事：

1. **全量数据下结果必须还是 2022-07-01** —— 这条改动不允许改变现有部署的默认日；
   原来 `schemas.py` 里 7 个模型各写死一份该字面量。
2. **只 cut 了三天的演示子集里不能挑到没有数据的日期**：子集轴 05-16T00Z..05-18T23Z
   要解析成 2022-05-17，也就是「有效数据的开头」而不是「时间轴的第一格」——
   首帧往前凑不齐 ±6h 脉动窗，选它会让演示日首尾成片判成窗口样本不足。
3. 数据完全读不到时返回 `settings.DEFAULT_DATE`，是一个能被 `np.datetime64` 解析的
   字符串而不是空串/None（下游 `f"{date}T08:00"` 之类拼接很多，空串会炸成 500）。
"""
import sys
import time
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings        # noqa: E402
from data_loader import DataLoader  # noqa: E402

HOUR = np.timedelta64(1, 'h')


def _loader_with(times):
    """造一个只带时间轴替身的 DataLoader（lat/lon 也给真的，_axis_bounds 要读）。"""
    ld = DataLoader()
    ld._loaded = True

    class _FakeAxis:
        def __init__(self, values):
            self.values = np.asarray(values)

    class _FakeDs:
        lat = _FakeAxis(np.array([29.0, 30.0, 31.0]))
        lon = _FakeAxis(np.array([90.0, 91.0, 92.0]))
        time = _FakeAxis(times)

    ld.ds_u = _FakeDs()
    return ld


def _axis(start, hours):
    return start + np.arange(hours) * HOUR


class TestResolveDefaultDate(unittest.TestCase):
    def test_full_year_still_prefers_july_first(self):
        """全年 8760 帧：默认日仍是 2022-07-01，与改前逐字相同。"""
        ld = _loader_with(_axis(np.datetime64('2022-01-01T00:00', 'ns'), 8760))
        self.assertEqual(ld.resolve_default_date(), '2022-07-01')

    def test_three_day_demo_subset_picks_the_demo_day(self):
        """演示子集（05-16T00Z 起 72 帧）：取 05-17，而不是首帧所在的 05-16。"""
        ld = _loader_with(_axis(np.datetime64('2022-05-16T00:00', 'ns'), 72))
        self.assertEqual(ld.resolve_default_date(), '2022-05-17')

    def test_leading_edge_without_window_pad_is_skipped(self):
        """轴从 05-16T12Z 开始：05-17 的前垫只有 12h < 需要的 14h，必须顺延到 05-18。"""
        ld = _loader_with(_axis(np.datetime64('2022-05-16T12:00', 'ns'), 24 * 4))
        self.assertEqual(ld.resolve_default_date(), '2022-05-18')

    def test_no_usable_day_falls_back_to_axis_start(self):
        """整条轴短到没有任何一天满足前后垫：回落到首日，而不是返回空串。"""
        ld = _loader_with(_axis(np.datetime64('2022-05-16T00:00', 'ns'), 12))
        self.assertEqual(ld.resolve_default_date(), '2022-05-16')

    def test_unparsable_by_datetime64_is_never_returned(self):
        """返回值必须能直接喂给 np.datetime64（下游按 f'{date}T08:00' 拼接）。"""
        for times in (_axis(np.datetime64('2022-01-01T00:00', 'ns'), 8760),
                      _axis(np.datetime64('2022-05-16T00:00', 'ns'), 72),
                      _axis(np.datetime64('2022-05-16T12:00', 'ns'), 96)):
            got = _loader_with(times).resolve_default_date()
            self.assertRegex(got, r'^\d{4}-\d{2}-\d{2}$')
            np.datetime64(got)          # 不可解析会在这里抛

    def test_missing_data_returns_configured_fallback(self):
        """数据读不到（且处于失败退避期内）：返回 settings.DEFAULT_DATE。"""
        ld = DataLoader()
        ld._loaded = False
        ld._load_failed_at = time.monotonic()
        self.assertEqual(ld.resolve_default_date(), settings.DEFAULT_DATE)


if __name__ == '__main__':
    unittest.main()
