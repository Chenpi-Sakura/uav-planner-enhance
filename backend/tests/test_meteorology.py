"""湍流量单入口的公式测试（申报书口径：脉动方差）。

这些用例盯的是「量的是什么」：旧实现把平均风的动能当成湍流，急流轴因此被
判成全域最强湍流区，地图与寻路的结论都是错的。阈值可以再标定，方向不能错。

运行：cd backend; .python/portable/python.exe -m unittest discover -s tests
"""
import os
import sys
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import numpy as np

import meteorology
from config import settings

HALF = settings.TURB_WINDOW_HALF_HOURS
NT = 2 * HALF + 1          # 中心帧恰好取满整个窗口


def _cube(rows):
    """把每小时的标量列表铺成 (hours, 1, 1) 的风场。"""
    return np.asarray(rows, dtype=float).reshape(len(rows), 1, 1)


class TestPulsationTke(unittest.TestCase):
    def test_uniform_jet_is_zero(self):
        """恒定强风不是湍流：40 m/s 不脉动 -> k = 0。"""
        u = np.full((NT, 1, 1), 40.0)
        z = np.zeros((NT, 1, 1))
        k = meteorology.pulsation_tke(u, z, z)
        self.assertTrue(np.all(np.isfinite(k)))
        self.assertAlmostEqual(float(k[HALF, 0, 0]), 0.0, places=9)

    def test_center_frame_matches_population_variance(self):
        """中心帧的 k 就是窗口内三分量方差之和的一半。"""
        u = _cube(range(NT))                      # 0..12，总体方差 (NT^2-1)/12
        z = np.zeros((NT, 1, 1))
        want_var = (NT * NT - 1) / 12.0
        k = meteorology.pulsation_tke(u, z, z)
        self.assertAlmostEqual(float(k[HALF, 0, 0]), 0.5 * want_var, places=6)

    def test_three_components_are_summed(self):
        """u/v/w 必须同层同格点一起进公式，缺一不可。"""
        ramp = _cube(range(NT))
        z = np.zeros((NT, 1, 1))
        v = ramp * 2.0
        k_u = meteorology.pulsation_tke(ramp, z, z)[HALF, 0, 0]
        k_uv = meteorology.pulsation_tke(ramp, v, z)[HALF, 0, 0]
        self.assertAlmostEqual(float(k_uv), float(k_u) * 5.0, places=6)

    def test_nan_target_hour_stays_nan(self):
        """目标小时缺测时结果必须是 NaN：下游靠它识别地形/无数据。"""
        u = _cube(range(NT))
        u[HALF, 0, 0] = np.nan
        z = np.zeros((NT, 1, 1))
        k = meteorology.pulsation_tke(u, z, z)
        self.assertTrue(np.isnan(k[HALF, 0, 0]))
        self.assertTrue(np.isfinite(k[HALF - 1, 0, 0]))

    def test_too_few_valid_samples_is_nan(self):
        """窗口内有效样本不足 min_samples 时不给值，避免用两三点凑方差。"""
        u = _cube([0.0] * NT)
        u[0, 0, 0] = np.nan          # 缺测落在窗口边缘，不落在目标小时上
        z = np.zeros((NT, 1, 1))
        full = meteorology.pulsation_tke(u, z, z, min_samples=NT)
        self.assertTrue(np.isnan(full[HALF, 0, 0]))
        relaxed = meteorology.pulsation_tke(u, z, z, min_samples=NT - 1)
        self.assertTrue(np.isfinite(relaxed[HALF, 0, 0]))

    def test_shape_mismatch_raises(self):
        u = np.zeros((NT, 1, 1))
        with self.assertRaises(ValueError):
            meteorology.pulsation_tke(u, np.zeros((NT, 2, 1)), u)

    def test_window_shorter_than_one_hour_raises(self):
        u = np.zeros((NT, 1, 1))
        with self.assertRaises(ValueError):
            meteorology.pulsation_tke(u, u, u, half_hours=0)


class TestEdrFromTke(unittest.TestCase):
    def test_edr_definition_identity(self):
        """EDR^3 == C_eps * k^1.5 / L，单位 m^(2/3)/s。"""
        k = 12.0
        edr = float(meteorology.edr_from_tke(k))
        eps = settings.TURB_EDR_C_EPS * k ** 1.5 / settings.TURB_EDR_LENGTH_SCALE_M
        self.assertAlmostEqual(edr, eps ** (1.0 / 3.0), places=9)
        self.assertGreater(edr, 0.0)

    def test_monotonic_and_zero_safe(self):
        edr = meteorology.edr_from_tke(np.array([-1.0, 0.0, 4.0, 16.0]))
        self.assertEqual(float(edr[0]), 0.0)          # 负值先夹到 0，不产生 NaN
        self.assertEqual(float(edr[1]), 0.0)
        self.assertTrue(np.all(np.diff(edr) >= 0))
        self.assertGreater(float(edr[3]), float(edr[2]))

    def test_nan_propagates(self):
        self.assertTrue(np.isnan(float(meteorology.edr_from_tke(np.nan))))


class TestSingleEntryPoint(unittest.TestCase):
    """公式只能有一份。

    旧代码在 7 处内联复制了 TKE/EDR，改一处漏六处，地图/寻路/曲线各说各话。
    这里用特征串把复制粘贴挡在门外。
    """

    SIGNATURES = ('0.01 * 0.5', '** 1.5', 'tke_arr')

    def test_no_inline_formula_outside_meteorology(self):
        offenders = []
        for name in sorted(os.listdir(BACKEND_DIR)):
            if not name.endswith('.py') or name == 'meteorology.py':
                continue
            with open(os.path.join(BACKEND_DIR, name), encoding='utf-8') as f:
                src = f.read()
            for sig in self.SIGNATURES:
                if sig in src:
                    offenders.append(f'{name}: {sig}')
        self.assertEqual(offenders, [], f'TKE/EDR 公式被复制回了这些地方 -> {offenders}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
