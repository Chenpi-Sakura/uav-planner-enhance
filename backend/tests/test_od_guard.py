"""入口几何门限：起终点不足一个气象格时按「输入不成立」拒绝，不交出 0 km 的"可飞"结论。"""
import unittest

from fastapi import HTTPException

import main
from schemas import Point, PlanRequest


def _req(dlon: float) -> PlanRequest:
    """同一纬度上拉开 dlon 度；29.65°N 处 1° 经度约 96.7 km。"""
    return PlanRequest(start=Point(lat=29.65, lon=91.14),
                       end=Point(lat=29.65, lon=91.14 + dlon))


class TestRequireValidOd(unittest.TestCase):
    def test_below_one_grid_cell_is_rejected(self):
        """0.02° ≈ 1.9 km，比一个格还小：沿途根本没有可判的格子。"""
        with self.assertRaises(HTTPException) as ctx:
            main._require_valid_od(_req(0.02))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("数据格", ctx.exception.detail)

    def test_one_grid_cell_passes(self):
        """0.06° ≈ 5.8 km，跨过 MIN_OD_KM=5 的门限。"""
        main._require_valid_od(_req(0.06))


if __name__ == '__main__':
    unittest.main()
