"""两阶段取数源：先只用子集，全量加载完毕后整体切到全量。

替身轴，不读真实 NetCDF。要守住的契约：

1. **没切之前只有子集**：可答的日期、覆盖框、界面默认日全都按子集那段报；
   子集之外的请求由现有的越界守卫判成**无数据**，而不是拿边界帧冒充（也不会半答）。
2. **切是全局单向的**：``full_source`` 加载完毕那一刻起，四条取数入口整次委派给它，
   不存在「同一轮规划里一半时刻取自子集、一半取自全量」——两边轴长不同，
   ±6h 脉动窗与越界守卫会算出不同的东西。
3. **没配子集时行为与单源时代逐字一致**（``full_source`` 为 None，``_active`` 恒返回 self）。
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

SUB_T0 = np.datetime64('2022-05-16T00:00', 'ns')
SUB_BOX = (28.0, 32.5, 87.5, 100.5)
FULL_T0 = np.datetime64('2022-01-01T00:00', 'ns')
FULL_BOX = (25.9, 40.3, 72.3, 103.2)
INSIDE = (29.0, 31.0, 90.0, 95.0)
JULY = np.datetime64('2022-07-01T00:00', 'ns')


def _loader(t0, frames, box):
    """只带轴的 DataLoader：``_axis_bounds`` 只读 ds_u 的三条轴。"""
    lats = np.linspace(box[0], box[1], 91)
    lons = np.linspace(box[2], box[3], 228)
    times = t0 + np.arange(frames) * HOUR

    class _Axis:
        def __init__(self, values):
            self.values = np.asarray(values)

        @property
        def size(self):
            return self.values.size

    class _Ds:
        lat = _Axis(lats)
        lon = _Axis(lons)
        time = _Axis(times)

    ld = DataLoader(data_dir=f'fake:{box}')
    ld._loaded = True
    ld.ds_u = _Ds()
    return ld


def _pair():
    """子集当主源、全量当待切换源。"""
    sub = _loader(SUB_T0, 72, SUB_BOX)
    full = _loader(FULL_T0, 8760, FULL_BOX)
    full._loaded = False          # 默认还没加载完，此时不许切
    sub.full_source = full
    return sub, full


class TestSwitch(unittest.TestCase):
    def test_subset_serves_while_full_loading(self):
        sub, full = _pair()
        self.assertFalse(sub.full_ready)
        self.assertIs(sub._active(), sub)

    def test_switches_once_full_is_loaded(self):
        sub, full = _pair()
        full._loaded = True
        self.assertTrue(sub.full_ready)
        self.assertIs(sub._active(), full)

    def test_switch_is_one_way(self):
        """切过去之后不会因为没有重新加载而回跳。"""
        sub, full = _pair()
        full._loaded = True
        self.assertIs(sub._active(), full)
        self.assertIs(sub._active(), full)

    def test_no_full_source_never_delegates(self):
        sub, _ = _pair()
        sub.full_source = None
        self.assertIs(sub._active(), sub)
        self.assertFalse(sub.full_ready)

    def test_warm_failure_keeps_subset(self):
        """全量加载失败（句柄起不来）时留在子集上，而不是切到一个空源。"""
        sub, full = _pair()
        full.ds_u = None
        full._loaded = False
        full._load_failed_at = time.monotonic()
        self.assertIs(sub._active(), sub)


class TestEntryPointsDelegate(unittest.TestCase):
    """四条取数入口在全量就绪后必须整次委派。"""

    def test_read_slab(self):
        sub, full = _pair()
        full._loaded = True
        full.read_slab = lambda *a: ('FROM_FULL',)
        self.assertEqual(sub.read_slab('u', JULY, 24, INSIDE)[0], 'FROM_FULL')

    def test_meteorology_grid(self):
        sub, full = _pair()
        full._loaded = True
        full.get_meteorology_grid = lambda *a, **k: ('FROM_FULL',)
        got = sub.get_meteorology_grid(*INSIDE, date='2022-07-01')
        self.assertEqual(got[0], 'FROM_FULL')

    def test_meteorology_at_points(self):
        sub, full = _pair()
        full._loaded = True
        full.get_meteorology_at_points = lambda *a, **k: {'from': 'FULL'}
        self.assertEqual(sub.get_meteorology_at_points([29.5], [91.5], ['2022-07-01T08:00']),
                         {'from': 'FULL'})

    def test_overlay(self):
        sub, full = _pair()
        full._loaded = True
        full.get_overlay_image = lambda *a, **k: b'FROM_FULL'
        self.assertEqual(sub.get_overlay_image(*INSIDE, 'tke', '2022-07-01'), b'FROM_FULL')

    def test_no_delegation_before_switch(self):
        """没切之前 read_slab 必须还在本源上走（用越界日期验，委派了就会看到 FROM_FULL）。"""
        sub, full = _pair()
        seen = {}
        full.read_slab = lambda *a: seen.setdefault('hit', True) or ('FROM_FULL',)
        got = sub.read_slab('u', JULY, 24, INSIDE)
        self.assertNotEqual(got[0], 'FROM_FULL')
        self.assertNotIn('hit', seen)


class TestMetaFollowsActiveSource(unittest.TestCase):
    def test_default_date_before_and_after_switch(self):
        sub, full = _pair()
        self.assertEqual(sub.resolve_default_date(), '2022-05-17')
        full._loaded = True
        self.assertEqual(sub.resolve_default_date(), '2022-07-01')

    def test_time_range_before_and_after_switch(self):
        sub, full = _pair()
        self.assertEqual(sub.time_range()[2], 72)
        full._loaded = True
        self.assertEqual(sub.time_range()[2], 8760)

    def test_bbox_before_and_after_switch(self):
        sub, full = _pair()
        self.assertLess(sub.coverage_bbox()['lon_max'], 101.0)
        full._loaded = True
        self.assertGreater(sub.coverage_bbox()['lon_max'], 103.0)

    def test_check_coverage_follows_active_source(self):
        """子集阶段点 102E 判「域外」是对的（那时真答不了）；切完必须判「域内」。"""
        sub, full = _pair()
        self.assertFalse(sub.check_coverage(30.0, 102.0))
        full._loaded = True
        self.assertTrue(sub.check_coverage(30.0, 102.0))

    def test_single_source_meta_is_its_own(self):
        sub, _ = _pair()
        sub.full_source = None
        self.assertEqual(sub.time_range()[2], 72)


class TestNoMixingInsideOneCall(unittest.TestCase):
    """切换不能落进一次取数的中间。

    这是实测踩出来的回归：修之前 ``get_meteorology_grid`` 里 u 取自子集、v/w 取自刚就绪的
    全量，``pulsation_tke`` 当场 ``ValueError: u/v/w 形状必须一致``，那一次规划请求 500。
    现在路由只在最外层做一次，内层走 ``_*_local``。

    桩必须打在**磁盘层**（``xr.open_dataset``）。早先这组用例替换的是
    ``_read_slab_local`` 本身，于是内层残留的那道 ``_active()`` 委派永远被执行不到，
    上面那个 500 从测试底下漏了出去 —— 替换的层就是盲区所在。
    """

    def _stub_disk(self, sub, full, flip_after_first_read=True):
        """让真正的 ``_read_slab_local`` 跑起来，记录每块 slab 出自哪个目录。"""
        import data_loader as dl_module

        hosts = {ld.data_dir: ld for ld in (sub, full)}
        reads: list = []

        class _Axis:
            def __init__(self, values):
                self.values = np.asarray(values)

        class _Vals:
            def __init__(self, a):
                self.values = a

        class _Var:
            def __init__(self, shape):
                self.shape = shape

            def isel(self, time=None, lev=None, lat=None, lon=None):
                t = np.arange(self.shape[0])[time]
                la = np.arange(self.shape[2])[lat]
                lo = np.arange(self.shape[3])[lon]
                return _Vals(np.zeros((t.size, la.size, lo.size), dtype='float32'))

        class _FakeDs:
            def __init__(self, ld):
                self.lat = _Axis(ld.ds_u.lat.values)
                self.lon = _Axis(ld.ds_u.lon.values)
                self.time = _Axis(ld.ds_u.time.values)
                self.lev = _Axis(np.array([500.0]))
                self._shape = (self.time.values.size, 1,
                               self.lat.values.size, self.lon.values.size)

            def __getitem__(self, var):
                return _Var(self._shape)

            def close(self):
                pass

        def open_dataset(path, *args, **kwargs):
            dirpath = next(d for d in hosts if d in path)
            reads.append(dirpath)
            if flip_after_first_read and len(reads) == 1:
                full._loaded = True      # 「u 刚落地，全量就绑好了」
            return _FakeDs(hosts[dirpath])

        original = dl_module.xr.open_dataset
        dl_module.xr.open_dataset = open_dataset
        self.addCleanup(setattr, dl_module.xr, 'open_dataset', original)
        return reads

    def test_grid_reads_all_three_vars_from_one_source(self):
        sub, full = _pair()
        reads = self._stub_disk(sub, full)
        tke, edr, lats, lons, times = sub.get_meteorology_grid(*INSIDE, date='2022-05-17')
        self.assertEqual(reads, [sub.data_dir] * 3)   # 中途就绪也不许插进来
        self.assertEqual(tke.shape, (len(times), lats.size, lons.size))

    def test_read_slab_entry_still_routes_when_full_is_ready(self):
        """切换已经发生：独立入口整块落到全量，不出现两边混读。"""
        sub, full = _pair()
        reads = self._stub_disk(sub, full, flip_after_first_read=False)
        full._loaded = True
        vals, lats, lons, times = sub.read_slab('u', JULY, 24, INSIDE)
        self.assertEqual(reads, [full.data_dir])
        self.assertEqual(vals.shape, (24, lats.size, lons.size))

    def test_field_entry_still_routes_when_not_pinned(self):
        """没被钉住的独立入口（时间窗口扫描直调）照常整体委派。"""
        sub, full = _pair()
        full._loaded = True
        seen = []
        full.read_turbulence_field = lambda *a, **k: seen.append(a) or (None,)
        self.assertEqual(sub.read_turbulence_field(JULY, 24, INSIDE)[0], None)
        self.assertEqual(len(seen), 1)


class TestOutOfSubsetIsNoDataNotHalf(unittest.TestCase):
    def test_grid_returns_empty_axes_for_july_while_only_subset(self):
        """只有子集时问 07-01：必须由越界守卫返回空轴（上游据此报 no_data），
        而不是 searchsorted 钳到末帧、拿 05-18 的值标着 07-01 交出去。"""
        sub, _ = _pair()
        tke, edr, lats, lons, times = sub.get_meteorology_grid(*INSIDE, date='2022-07-01')
        self.assertEqual(times.size, 0)
        self.assertEqual(lats.size, 0)
        self.assertEqual(tke.shape, (0, 0, 0))

    def test_unreadable_data_still_returns_the_fallback_date_string(self):
        ld = DataLoader()
        ld._loaded = False
        ld._load_failed_at = time.monotonic()
        self.assertEqual(ld.resolve_default_date(), settings.DEFAULT_DATE)


if __name__ == '__main__':
    unittest.main()
