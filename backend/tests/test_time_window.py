"""
功能二 · 时间窗口规划的自动化测试（标准库 unittest，不依赖真实数据）。

运行：
    python -m unittest discover -s backend/tests -v
"""
import os
import sys
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import numpy as np

import meteorology
import time_window as tw
from config import settings
from planner import PlanResult
from risk import LEVEL_LOW, LEVEL_MODERATE, LEVEL_HIGH, LEVEL_UNFLYABLE
from schemas import Point

# 合成数据用的三档湍流量。写死 0.1/0.5/1.5 那套数字属于旧的「平均风动能」刻度，
# 阈值换到脉动方差口径后会整体失配，因此改成从 settings 推导。
SAFE_TKE = settings.TKE_RISK_MODERATE * 0.2        # 安全区
MODERATE_TKE = settings.TKE_RISK_MODERATE * 1.5    # 中风险：可飞但记入等级
UNSAFE_TKE = settings.TKE_THRESHOLD_HIGH * 1.5     # 超硬约束：不可飞


def _synth_slab(tke_by_hour, n_lat=5, n_lon=5, base='2022-07-01T00:00:00'):
    """构造合成数据块：每小时一个标量风险，铺满整个网格。

    tke_by_hour: 长度为 nT 的列表，元素为 None（表示 NaN 缺失）或数值。
    """
    nT = len(tke_by_hour)
    tke = np.zeros((nT, n_lat, n_lon))
    for i, v in enumerate(tke_by_hour):
        tke[i] = np.nan if v is None else float(v)
    edr = meteorology.edr_from_tke(np.where(np.isnan(tke), np.nan, tke))
    lats = np.linspace(29.0, 29.2, n_lat)
    lons = np.linspace(91.0, 91.2, n_lon)
    times = np.array([np.datetime64(base) + np.timedelta64(i, 'h') for i in range(nT)])
    return tke, edr, lats, lons, times


def _samples(n=10, span_km=100.0):
    lats = np.linspace(29.0, 29.1, n)
    lons = np.linspace(91.0, 91.2, n)
    cum = np.linspace(0.0, span_km, n)
    return lats, lons, cum


class TestTimezone(unittest.TestCase):
    """时区换算：输入按北京时间、数据标签按 UTC。"""

    def test_roundtrip_with_utc_label(self):
        saved = settings.DATA_LABEL_IS_UTC
        try:
            settings.DATA_LABEL_IS_UTC = True
            data_t = tw.to_data_time('2022-07-01T08:00')
            self.assertEqual(str(data_t)[:16], '2022-07-01T00:00')
            back = tw.to_user_time(data_t)
            self.assertEqual(str(back)[:16], '2022-07-01T08:00')
        finally:
            settings.DATA_LABEL_IS_UTC = saved

    def test_no_shift_when_disabled(self):
        saved = settings.DATA_LABEL_IS_UTC
        try:
            settings.DATA_LABEL_IS_UTC = False
            t = tw.to_data_time('2022-07-01T08:00')
            self.assertEqual(str(t)[:16], '2022-07-01T08:00')
            self.assertEqual(str(tw.to_user_time(t))[:16], '2022-07-01T08:00')
        finally:
            settings.DATA_LABEL_IS_UTC = saved

    def test_label_is_beijing_time(self):
        saved = settings.DATA_LABEL_IS_UTC
        try:
            settings.DATA_LABEL_IS_UTC = True
            # 数据时间 23:00 UTC -> 北京时间次日 07:00
            self.assertEqual(tw._fmt_hhmm(np.datetime64('2022-07-01T23:00')), '07:00')
        finally:
            settings.DATA_LABEL_IS_UTC = saved


class TestSampling(unittest.TestCase):
    """沿路径等距采样。"""

    def test_resample_line(self):
        path = [{'lat': 29.0, 'lon': 91.0}, {'lat': 29.0, 'lon': 92.0}]
        lats, lons, cum, hit = tw.sample_along_path(path, step_deg=0.05)
        self.assertGreater(len(cum), 2)
        self.assertAlmostEqual(cum[0], 0.0, places=6)
        self.assertTrue(hit[0] and hit[-1])
        # 累计距离应单调递增
        self.assertTrue(np.all(np.diff(cum) >= 0))

    def test_too_few_points(self):
        lats, lons, cum, hit = tw.sample_along_path([{'lat': 29.0, 'lon': 91.0}])
        self.assertEqual(len(cum), 0)


class TestScanSlots(unittest.TestCase):
    """逐起点时刻扫描的全程安全性判定。"""

    def _scan(self, tke_by_hour, n_slots=24, step_minutes=60, speed=60.0, pad_hours=2):
        """扫描槽位。

        真实链路里 slab 会按飞行时长向外扩展（见 assess_time_window 的 total_hours），
        否则末段采样点的到达时刻会越界并被显式判为不可评估。
        这里同样补一段 pad，让测试反映真实条件。
        """
        padded = list(tke_by_hour) + [tke_by_hour[-1]] * int(pad_hours)
        tke, edr, lats, lons, times = _synth_slab(padded)
        slats, slons, cum = _samples(n=5, span_km=60.0)
        return tw.scan_slots(tke, edr, times, slats, slons, lats, lons, cum, speed,
                             np.datetime64('2022-07-01T00:00:00'), n_slots, step_minutes,
                             settings.TKE_THRESHOLD_HIGH, settings.EDR_THRESHOLD_HIGH)

    def test_all_safe(self):
        slots = self._scan([SAFE_TKE] * 24)
        self.assertEqual(len(slots), 24)
        self.assertTrue(all(s.safe for s in slots))
        self.assertEqual(slots[0].level, LEVEL_LOW)

    def test_all_unsafe_by_turbulence(self):
        slots = self._scan([UNSAFE_TKE] * 24)
        self.assertFalse(any(s.safe for s in slots))
        self.assertTrue(all(s.level == LEVEL_UNFLYABLE for s in slots))

    def test_nan_marks_unsafe(self):
        slots = self._scan([None] * 24)
        self.assertFalse(any(s.safe for s in slots))
        self.assertIsNone(slots[0].max_tke)

    def test_time_progression_picks_later_hour(self):
        # 前 3 小时危险、之后安全；飞行 1 小时（span 60km / 60km/h）
        # 起飞越晚，途中经过危险时段的概率越低 -> 应存在安全槽位且趋势正确
        hourly = [UNSAFE_TKE] * 3 + [SAFE_TKE] * 21
        slots = self._scan(hourly)
        safe_idx = [i for i, s in enumerate(slots) if s.safe]
        self.assertTrue(safe_idx, "应存在安全起飞时刻")
        self.assertFalse(slots[0].safe)
        self.assertFalse(slots[2].safe)
        self.assertGreaterEqual(min(safe_idx), 3)

    def test_moderate_level_reported(self):
        slots = self._scan([MODERATE_TKE] * 24)
        self.assertTrue(all(s.safe for s in slots))
        self.assertTrue(all(s.level == LEVEL_MODERATE for s in slots))


class TestWindowAggregation(unittest.TestCase):
    """窗口聚合与推荐选择。"""

    def _mk(self, safe_pattern, tke=0.1):
        slots = []
        for i, ok in enumerate(safe_pattern):
            slots.append(tw.SlotResult(
                index=i,
                data_time=np.datetime64('2022-07-01T00:00') + np.timedelta64(i, 'h'),
                safe=ok,
                max_tke=(tke + i * 0.01) if ok else None,
                max_edr=0.01 if ok else None,
                level=LEVEL_LOW if ok else LEVEL_UNFLYABLE,
            ))
        return slots

    def test_contiguous_merge(self):
        slots = self._mk([False, True, True, True, False, True, True, False])
        wins = tw.aggregate_windows(slots)
        self.assertEqual(len(wins), 2)
        self.assertEqual((wins[0].start_index, wins[0].end_index), (1, 3))
        self.assertEqual((wins[1].start_index, wins[1].end_index), (5, 6))

    def test_no_window_when_none_safe(self):
        self.assertEqual(tw.aggregate_windows(self._mk([False] * 5)), [])

    def test_pick_lowest_risk(self):
        slots = self._mk([True] * 3 + [False] + [True] * 3)
        # 手工压低第二个窗口的风险
        for i in (4, 5, 6):
            slots[i].max_tke = 0.05
        wins = tw.aggregate_windows(slots)
        best = tw.pick_recommended(wins)
        self.assertEqual(best.start_index, 4)

    def test_pick_tie_takes_earlier(self):
        slots = self._mk([True, True, False, True, True])
        for s in slots:
            if s.safe:
                s.max_tke = 0.2
        wins = tw.aggregate_windows(slots)
        self.assertEqual(tw.pick_recommended(wins).start_index, 0)

    def test_backup_excludes_recommended_and_limits(self):
        slots = self._mk([True, False, True, False, True, False, True])
        wins = tw.aggregate_windows(slots)
        rec = tw.pick_recommended(wins)
        backups = tw.rank_backup_windows(wins, rec, limit=2)
        self.assertEqual(len(backups), 2)
        self.assertTrue(all(w.start_index != rec.start_index for w in backups))

    def test_window_labels_are_beijing(self):
        saved = settings.DATA_LABEL_IS_UTC
        try:
            settings.DATA_LABEL_IS_UTC = True
            slots = self._mk([True, True])
            w = tw.aggregate_windows(slots)[0]
            # 数据时间 00:00 / 01:00 UTC -> 北京时间 08:00 / 09:00
            self.assertEqual(w.start_label, '08:00')
            self.assertEqual(w.end_label, '09:00')
        finally:
            settings.DATA_LABEL_IS_UTC = saved


class TestCurve(unittest.TestCase):
    """湍流曲线构建。"""

    def test_curve_shape_and_labels(self):
        tke, edr, lats, lons, times = _synth_slab([SAFE_TKE] * 24)
        slats, slons, _ = _samples(n=4)
        curve = tw.build_curve(tke, edr, times, slats, slons, lats, lons,
                              np.datetime64('2022-07-01T00:00'),
                              np.datetime64('2022-07-01T02:00'), 30)
        # 00:00–02:00 每 30 分钟 -> 5 个点
        self.assertEqual(len(curve['times']), 5)
        self.assertEqual(len(curve['max_tke']), 5)
        self.assertEqual(curve['times'][0], '08:00')
        self.assertAlmostEqual(curve['max_tke'][0], SAFE_TKE, places=3)

    def test_empty_on_no_data(self):
        curve = tw.build_curve(np.array([]), np.array([]), np.array([]),
                              np.array([]), np.array([]), np.array([]), np.array([]),
                              np.datetime64('2022-07-01T00:00'),
                              np.datetime64('2022-07-01T01:00'), 30)
        self.assertEqual(curve['times'], [])


class TestGeometryBlocked(unittest.TestCase):
    """几何永久阻塞判定（直接检查几何，而非从槽位结果反推）。"""

    def test_detects_always_nan_cell(self):
        tke, edr, lats, lons, times = _synth_slab([SAFE_TKE] * 24, n_lat=3, n_lon=3)
        tke[:, 1, 1] = np.nan
        edr[:, 1, 1] = np.nan
        slats = np.array([lats[0], lats[1], lats[2]])
        slons = np.array([lons[0], lons[1], lons[2]])
        self.assertTrue(tw.geometry_blocked(tke, edr, times, lats, lons, slats, slons))

    def test_clear_geometry_is_not_blocked(self):
        tke, edr, lats, lons, times = _synth_slab([SAFE_TKE] * 24, n_lat=3, n_lon=3)
        slats = np.array([lats[0], lats[1], lats[2]])
        slons = np.array([lons[0], lons[1], lons[2]])
        self.assertFalse(tw.geometry_blocked(tke, edr, times, lats, lons, slats, slons))

    def test_partial_nan_is_not_permanent(self):
        """只有个别时次缺失，不算永久阻塞。"""
        hourly = [SAFE_TKE] * 24
        tke, edr, lats, lons, times = _synth_slab(hourly, n_lat=3, n_lon=3)
        tke[5, 1, 1] = np.nan
        edr[5, 1, 1] = np.nan
        slats = np.array([lats[0], lats[1], lats[2]])
        slons = np.array([lons[0], lons[1], lons[2]])
        self.assertFalse(tw.geometry_blocked(tke, edr, times, lats, lons, slats, slons))

class TWStubbed(unittest.TestCase):
    """公共替身：既不碰 NetCDF 也不跑真 A*，只验编排层的分支与字段。"""

    class _Req:
        def __init__(self, **kw):
            # 必须是真的 schemas.Point：Top-K 会拿它构造 PlanRequest，
            # 用鸭子类型的替身会在 pydantic 校验上炸 —— 而 _geometry 的
            # 那层 except Exception 会把它伪装成「直线兜底」悄悄过去。
            self.start = Point(lat=29.0, lon=91.0)
            self.end = Point(lat=29.1, lon=91.2)
            self.waypoints = []
            self.date = '2022-07-01'
            self.start_time = '08:00'
            self.speed = 60.0
            self.forecast_days = kw.get('forecast_days', 2)
            self.scan_step_hours = kw.get('scan_step_hours', 1)
            self.tke_threshold = None
            self.edr_threshold = None

    def setUp(self):
        self._orig_load = tw._load_turbulence
        self._orig_geom = tw._geometry
        self._orig_cov = tw.data_loader.check_coverage
        self._orig_warn = tw._warnings
        self._orig_plan = tw.plan_path_ex

    def tearDown(self):
        tw._load_turbulence = self._orig_load
        tw._geometry = self._orig_geom
        tw.data_loader.check_coverage = self._orig_cov
        tw._warnings = self._orig_warn
        tw.plan_path_ex = self._orig_plan

    def _stub_plans(self, tke_values=None, distances=None, found=None):
        """把 Top-K 用的规划换成桩：记录每个候选收到的 date/start_time，返回合成航线。

        三个参数都可以给标量或列表（列表按调用顺序取，超出部分沿用最后一项），
        这样能人为制造「粗排与细排结论相反」的场景来钉排序口径。
        """
        self.plan_requests = []
        self.plan_fields = []
        calls = {'i': 0}

        def pick(spec, default):
            if spec is None:
                return default
            i = min(calls['i'], len(spec) - 1) if isinstance(spec, (list, tuple)) else 0
            return spec[i] if isinstance(spec, (list, tuple)) else spec

        def fake_plan(plan_req, field=None):
            self.plan_requests.append((plan_req.date, plan_req.start_time,
                                       plan_req.priority))
            self.plan_fields.append(field)
            tke = float(pick(tke_values, SAFE_TKE))
            km = float(pick(distances, 200.0))
            ok = bool(pick(found, True))
            path = ([Point(lat=29.0, lon=91.0), Point(lat=29.1, lon=91.2)] if ok else [])
            n = max(1, len(path))
            result = PlanResult(
                path=path, total_distance=km if ok else 0.0, total_grid_cost=0.0,
                found=ok, reason=None if ok else 'blocked',
                times=[np.datetime64('2022-07-01T00:00')] * n,
                tkes=[tke] * n, edrs=[float(meteorology.edr_from_tke(tke))] * n,
                waypoints_reached=[1] if ok else [], segment_results=[],
            )
            calls['i'] += 1
            return result

        tw.plan_path_ex = fake_plan

    def _install(self, tke_by_hour, fallback=False, stub_warnings=True, plan=None):
        """安装桩函数。

        注意：合成数据的 time 轴必须以「请求的数据起始时刻」为基准，
        与真实数据读取的行为一致，否则曲线/扫描会落在切片之外。
        """
        def fake_load(data_start, total_hours, bbox):
            n = max(len(tke_by_hour), int(total_hours))
            padded = list(tke_by_hour) + [tke_by_hour[-1]] * (n - len(tke_by_hour))
            tk, ed, la, lo, _ = _synth_slab(padded[:n], base=str(np.datetime64(data_start)))
            return tk, ed, la, lo, np.array(
                [np.datetime64(data_start) + np.timedelta64(i, 'h') for i in range(n)])

        tw._load_turbulence = fake_load
        tw._geometry = lambda *a, **k: ([{'lat': 29.0, 'lon': 91.0},
                                         {'lat': 29.1, 'lon': 91.2}], fallback)
        tw.data_loader.check_coverage = lambda *a, **k: True
        if stub_warnings:
            # 预警会读真实数据文件，单元测试里直接桩掉
            tw._warnings = lambda *a, **k: []
        # Top-K 会真的去规划，桩掉免得碰 NetCDF / A*
        self._stub_plans(**(plan or {}))


class TestAssessTimeWindow(TWStubbed):
    """编排层：用替换掉 data_loader / planner 的桩函数验证分支。"""

    def test_has_window_branch(self):
        self._install([SAFE_TKE] * 24 * 4)
        res = tw.assess_time_window(self._Req())
        self.assertTrue(res['has_window'])
        self.assertIsNotNone(res['recommended'])
        self.assertEqual(res['reason'], None)
        self.assertTrue(len(res['slot_analysis']) == 24)
        self.assertTrue(len(res['curve']['times']) > 0)

    def test_no_window_then_forecast(self):
        # 当日与次日全天危险，第三天安全 -> 最佳备选日期应落在第三天
        hourly = [UNSAFE_TKE] * 24 + [UNSAFE_TKE] * 24 + [SAFE_TKE] * 24
        self._install(hourly)
        res = tw.assess_time_window(self._Req(forecast_days=2))
        self.assertFalse(res['has_window'])
        self.assertEqual(res['reason'], tw.REASON_NO_SAFE_SLOT)
        self.assertTrue(res['forecast']['enabled'])
        self.assertEqual(len(res['forecast']['days']), 2)
        self.assertEqual(res['forecast']['days'][0]['has_window'], False)
        self.assertEqual(res['forecast']['days'][0]['date'], '2022-07-02')
        self.assertEqual(res['forecast']['days'][1]['has_window'], True)
        self.assertEqual(res['forecast']['first_available_date'], '2022-07-03')

    def test_no_window_any_day(self):
        self._install([UNSAFE_TKE] * 24 * 4)
        res = tw.assess_time_window(self._Req(forecast_days=2))
        self.assertFalse(res['has_window'])
        self.assertIsNone(res['forecast']['first_available_date'])
        self.assertEqual(res['reason'], tw.REASON_NO_SAFE_SLOT)

    def test_permanently_blocked_when_geometry_dead(self):
        """几何穿越全天候缺失格点 -> 永久阻塞。"""
        self._install([None] * 24 * 4)
        res = tw.assess_time_window(self._Req(forecast_days=2))
        self.assertFalse(res['has_window'])
        self.assertEqual(res['reason'], tw.REASON_PERMANENTLY_BLOCKED)
        self.assertTrue(res['meta']['geometry_blocked'])

    def test_no_safe_slot_when_only_turbulence_exceeds(self):
        """只是湍流超标（数据存在）-> 全天无安全时段，不得报永久阻塞。"""
        self._install([UNSAFE_TKE] * 24 * 4)
        res = tw.assess_time_window(self._Req(forecast_days=2))
        self.assertFalse(res['has_window'])
        self.assertEqual(res['reason'], tw.REASON_NO_SAFE_SLOT)
        self.assertFalse(res['meta']['geometry_blocked'])



    def test_data_coverage_insufficient(self):
        self._install([SAFE_TKE] * 24 * 4)
        tw.data_loader.check_coverage = lambda *a, **k: False
        res = tw.assess_time_window(self._Req())
        self.assertFalse(res['has_window'])
        self.assertEqual(res['reason'], tw.REASON_DATA_MISSING)

    def test_geometry_fallback_flag(self):
        self._install([SAFE_TKE] * 24 * 4, fallback=True)
        res = tw.assess_time_window(self._Req())
        self.assertTrue(res['meta']['geometry_fallback'])

    def test_scan_step_configurable(self):
        self._install([SAFE_TKE] * 24 * 4)
        res = tw.assess_time_window(self._Req(scan_step_hours=3))
        self.assertEqual(len(res['slot_analysis']), 8)
        self.assertEqual(res['meta']['scan_step_hours'], 3)


class TestRegressionFixes(unittest.TestCase):
    """针对联调中实际发现的两个缺陷的回归测试。"""

    def test_recommend_prefers_best_slot_not_worst(self):
        """推荐窗口应看「窗口内最优槽位」，而不是窗口整体最差风险。

        场景：窗口 A 只有 1 个高风险槽位（0.86）；窗口 B 有若干槽位，
        其中一个是低风险（0.27）。需求要求「寻找湍流强度最低的窗口期」，
        因此应推荐窗口 B。
        """
        slots = []
        for i in range(6):
            if i == 0:
                slots.append(tw.SlotResult(i, np.datetime64('2022-07-01T00:00') + np.timedelta64(i, 'h'),
                                           True, 0.86, 0.004, LEVEL_HIGH))
            elif i == 1:
                slots.append(tw.SlotResult(i, np.datetime64('2022-07-01T00:00') + np.timedelta64(i, 'h'),
                                           False, None, None, LEVEL_UNFLYABLE))
            else:
                tke = 0.27 if i == 2 else 0.45
                slots.append(tw.SlotResult(i, np.datetime64('2022-07-01T00:00') + np.timedelta64(i, 'h'),
                                           True, tke, 0.002, LEVEL_LOW if i == 2 else LEVEL_MODERATE))
        wins = tw.aggregate_windows(slots)
        self.assertEqual(len(wins), 2)
        best = tw.pick_recommended(wins, slots)
        self.assertEqual(best.start_index, 2)
        self.assertEqual(slots[best.best_slot].max_tke, 0.27)

    def test_scan_slots_flags_out_of_range(self):
        """到达时刻超出 slab 范围时必须判为不可评估，不得吸附到边界值。"""
        # 只有 6 小时数据，但扫描 24 个槽位、每个槽位飞行 3 小时
        tke, edr, lats, lons, times = _synth_slab([SAFE_TKE] * 6)
        slats, slons, cum = _samples(n=5, span_km=180.0)   # 180km / 60km/h = 3h
        slots = tw.scan_slots(tke, edr, times, slats, slons, lats, lons, cum, 60.0,
                              np.datetime64('2022-07-01T00:00:00'), 24, 60,
                              settings.TKE_THRESHOLD_HIGH, settings.EDR_THRESHOLD_HIGH)
        self.assertEqual(len(slots), 24)
        # 起飞时刻 00:00–03:00 的到达时刻仍在 6 小时范围内
        self.assertTrue(slots[0].safe)
        # 起飞时刻 04:00 之后到达时刻超出范围 -> 不可评估
        self.assertIsNone(slots[23].max_tke)
        self.assertFalse(slots[23].safe)
        # 不能出现「尾部槽位数值全部相同」的边界吸附假象
        tail = [s.max_tke for s in slots if s.max_tke is not None]
        self.assertLess(len(tail), 24)

    def test_narrative_nan_points_do_not_fake_safety(self):
        """NaN 采样点必须使该槽位不可飞（与 planner 硬约束口径一致）。"""
        hourly = [SAFE_TKE] * 24
        tke, edr, lats, lons, times = _synth_slab(hourly, n_lat=3, n_lon=3)
        tke[:, 1, 1] = np.nan      # 中间格点缺失
        edr[:, 1, 1] = np.nan
        slats = np.array([lats[0], lats[1], lats[2]])
        slons = np.array([lons[0], lons[1], lons[2]])
        cum = np.array([0.0, 5.0, 10.0])
        slots = tw.scan_slots(tke, edr, times, slats, slons, lats, lons, cum, 60.0,
                              np.datetime64('2022-07-01T00:00:00'), 4, 60,
                              settings.TKE_THRESHOLD_HIGH, settings.EDR_THRESHOLD_HIGH)
        self.assertTrue(all(not s.safe for s in slots))


class TestWarningsGraceful(unittest.TestCase):
    """预警：读取不到数据时应安静返回空列表，而不是抛错。"""

    def test_no_data_returns_empty(self):
        import time_window as twm
        orig = twm.data_loader.read_slab
        try:
            twm.data_loader.read_slab = lambda *a, **k: (None, None, None, None)
            out = twm._warnings(np.array([29.0]), np.array([91.0]),
                                np.datetime64('2022-07-01T00:00'),
                                np.datetime64('2022-07-01T05:00'),
                                (28.0, 30.0, 90.0, 92.0))
            self.assertEqual(out, [])
        finally:
            twm.data_loader.read_slab = orig


class TestTopKRoutes(TWStubbed):
    """B10：粗筛之后，Top-K 候选各按【自身起飞时刻】规划真实航线，再按真实航线重排。

    合成场景给出三个互不相邻的安全窗口（北京时 00:00 / 02:00 / 04:00），
    于是「粗排冠军」与「细排冠军」可以被人为拧反 —— 这正是排序口径要钉的地方。
    """

    PATTERN = ([SAFE_TKE, UNSAFE_TKE, MODERATE_TKE, UNSAFE_TKE, SAFE_TKE]
               + [UNSAFE_TKE] * 19)

    def _assess(self, **plan):
        self._install(self.PATTERN, plan=plan)
        return tw.assess_time_window(self._Req())

    def test_three_windows_give_three_candidates(self):
        res = self._assess()
        self.assertEqual(len(res['backup_windows']), 2)
        self.assertEqual(len(res['routes']), 3)
        self.assertEqual(len(self.plan_requests), 3)
        self.assertEqual(res['meta']['top_k_planned'], 3)

    def test_each_candidate_uses_its_own_takeoff_in_beijing_time(self):
        """规划请求里的时刻必须是【北京时间口径的该候选自身起飞时刻】。

        数据时间轴比北京早 8 小时，忘了换算就会去规划 16:00/18:00/20:00 三条线。
        """
        res = self._assess()
        given = {f'{d}T{t}' for d, t, _ in self.plan_requests}
        self.assertEqual(given, {'2022-07-01T00:00', '2022-07-01T02:00',
                                 '2022-07-01T04:00'})
        self.assertEqual(given, {r['takeoff'] for r in res['routes']})

    def test_routes_only_plan_the_balance_tier(self):
        self._assess()
        self.assertEqual({p for _, _, p in self.plan_requests},
                         {settings.DEFAULT_PRIORITY})

    def test_k_caps_candidates_but_not_backup_slots(self):
        saved = settings.TW_TOP_K_ROUTES
        settings.TW_TOP_K_ROUTES = 2
        try:
            res = self._assess()
        finally:
            settings.TW_TOP_K_ROUTES = saved
        self.assertEqual(len(res['routes']), 2)
        self.assertEqual(len(self.plan_requests), 2)
        self.assertEqual(len(res['backup_windows']), 2, "备用时段仍按粗排全给，不该被 K 砍掉")

    def test_fine_rank_prefers_lower_risk_over_shorter_distance(self):
        """粗排冠军规划出来是中风险 200 km，次候选是安全 250 km —— 必须选后者。

        风险与里程不能混成一个加权键（backlog B1 留档第 2 条）。
        """
        res = self._assess(tke_values=[MODERATE_TKE, SAFE_TKE, MODERATE_TKE],
                           distances=[200.0, 250.0, 300.0])
        best = res['routes'][0]
        self.assertEqual(best['takeoff'], '2022-07-01T04:00')
        self.assertEqual(best['overall_level'], LEVEL_LOW)
        self.assertEqual(res['recommended_final']['takeoff'], '2022-07-01T04:00')
        self.assertEqual(res['recommended_final']['level'], LEVEL_LOW)
        self.assertEqual(res['recommended']['takeoff'], '2022-07-01T00:00',
                         "粗排结果原样保留，供前端展示「走廊级 vs 真实航线」的差异")

    def test_curve_follows_the_final_recommendation(self):
        res = self._assess(tke_values=[MODERATE_TKE, SAFE_TKE, MODERATE_TKE],
                           distances=[200.0, 250.0, 300.0])
        # 曲线从细排冠军起飞前 1 h 起算；若仍跟着粗排冠军走，起点会是 00:00
        self.assertEqual(res['curve']['times'][0], '03:00')

    def test_one_big_window_still_fills_k_candidates(self):
        """全天安全会合并成一个窗口，Top-K 只剩 1 条 —— 必须在窗口内补点凑满。

        否则 B10 的原始抱怨（「我要 14:00 起飞的那条线」）在最常见的场景里没被解决。
        """
        self._install([SAFE_TKE] * 24)
        res = tw.assess_time_window(self._Req())
        self.assertEqual(len(res['backup_windows']), 0, "合成场景本就只有一个连续窗口")
        self.assertEqual(len(res['routes']), int(settings.TW_TOP_K_ROUTES))
        takeoffs = [r['takeoff'] for r in res['routes']]
        self.assertEqual(len(set(takeoffs)), len(takeoffs), "补出来的时刻必须互不相同")
        safe_iso = {'2022-07-01T' + s['time'] for s in res['slot_analysis'] if s['safe']}
        self.assertLessEqual(set(takeoffs), safe_iso, "只能取扫描确认过安全的整点")
        self.assertIn(res['recommended']['takeoff'], takeoffs, "粗排冠军不能被挤掉")

    def test_equal_tier_and_distance_break_on_peak_tke_not_clock(self):
        """档位与里程都一样时，要比沿程最坏点，而不是让时钟决定冠军。

        候选顺序是 00:00 / 04:00 / 02:00，给它们的 TKE 峰值分别是 12 / 9 / 15.2，
        三条都是中风险、里程写死一样 —— 冠军必须是 04:00 那条。
        """
        m = settings.TKE_RISK_MODERATE
        res = self._assess(tke_values=[m * 1.5, m * 1.125, m * 1.9],
                           distances=[200.0, 200.0, 200.0])
        # 三条给同样的档位（具体是中风险还是被 moderate_ratio 抬成高风险不重要，
        # 重要的是它们相等），这样比较的才只能是「沿程最坏点」。
        self.assertEqual(len({r['overall_level'] for r in res['routes']}), 1)
        self.assertEqual(res['routes'][0]['takeoff'], '2022-07-01T04:00')

    def test_infeasible_candidate_stays_visible_and_ranks_last(self):
        res = self._assess(found=[True, False, True], distances=[200.0, 200.0, 300.0])
        by_takeoff = {r['takeoff']: r for r in res['routes']}
        self.assertEqual(len(by_takeoff), 3, "不可飞的候选不能悄悄消失")
        bad = by_takeoff['2022-07-01T04:00']
        self.assertFalse(bad['feasible'])
        self.assertIsNotNone(bad['reason'])
        self.assertEqual(res['routes'][-1]['takeoff'], '2022-07-01T04:00')

    def test_scan_field_is_injected_into_every_candidate(self):
        """C：Top-K 复用第一遍读好的扫描场，五元组的顺序不能错位。"""
        res = self._assess()
        self.assertEqual(res['meta']['top_k_field'], 'injected')
        self.assertEqual(len(self.plan_fields), 3)
        for f in self.plan_fields:
            self.assertIsNotNone(f, "每个候选都要拿到注入场")
            tke, edr, la, lo, ts = f
            self.assertEqual(tke.shape, edr.shape)
            self.assertEqual(tke.shape[0], ts.size)
            self.assertEqual(tke.shape[1], la.size)
            self.assertEqual(tke.shape[2], lo.size)


if __name__ == '__main__':
    unittest.main()