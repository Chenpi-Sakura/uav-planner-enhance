"""HTTP 入口的两道门：起终点不足一个气象格、机型名认不出来，都在入口按「输入不成立」拒掉，
而不是交出一条 0 km 的"可飞"结论或让 ValueError 变成 500。"""
import unittest

from fastapi import HTTPException

import main
from schemas import Point, PlanRequest, SitePlanRequest


def _req(dlon: float) -> PlanRequest:
    """同一纬度上拉开 dlon 度；29.65°N 处 1° 经度约 96.7 km。"""
    return PlanRequest(start=Point(lat=29.65, lon=91.14),
                       end=Point(lat=29.65, lon=91.14 + dlon))


def _site_req(aircraft) -> SitePlanRequest:
    return SitePlanRequest(start=Point(lat=29.65, lon=91.14),
                           end=Point(lat=29.66, lon=94.36), aircraft=aircraft)


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


class TestKnownAircraft(unittest.TestCase):
    def test_unknown_aircraft_is_rejected_at_the_gate(self):
        with self.assertRaises(HTTPException) as ctx:
            main._require_known_aircraft(_site_req("b-52"))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("未知机型", ctx.exception.detail)

    def test_blank_aircraft_is_the_default_pack_not_an_error(self):
        """留空 = 用 SITE_DEFAULT_AIRCRAFT，与填了错名是两回事，不该在这里被拒。"""
        main._require_known_aircraft(_site_req(None))


if __name__ == '__main__':
    unittest.main()
