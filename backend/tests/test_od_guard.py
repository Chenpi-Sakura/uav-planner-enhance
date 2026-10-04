"""HTTP 入口的两道门：起终点不足一个气象格、机型名认不出来，都在入口按「输入不成立」拒掉，
而不是交出一条 0 km 的"可飞"结论或让 ValueError 变成 500。"""
import json
import unittest

from fastapi import HTTPException

import main
from config import settings
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


class TestFetchElevations(unittest.TestCase):
    """高程回填必须按**航点下标**对位，不能靠 append 累加。

    opentopodata 对无效坐标可能少返回 ``results`` 条数，而 HTTP 状态仍是 200。
    旧写法 append 累加，于是少一条就让后面所有点的高程整体前移一格，
    剖面图照样画得出来，只是每条点的高程都挂在隔壁点上。

    这里不发真实请求：把 ``urllib.request.urlopen`` 换成返回构造好的 payload。
    """

    def setUp(self):
        self.saved_urlopen = main.urllib.request.urlopen

    def tearDown(self):
        main.urllib.request.urlopen = self.saved_urlopen

    def _install(self, payloads):
        """payloads: 每个 chunk 调用返回的 api_data（list，按请求顺序）。"""
        calls = []

        def _fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            payload = payloads[len(calls) - 1]

            class _Ctx:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *a):
                    return False

                def read(self_inner):
                    return json.dumps(payload).encode()

            return _Ctx()

        main.urllib.request.urlopen = _fake_urlopen
        return calls

    @staticmethod
    def _res(lat, lon, elev):
        return {"elevation": elev, "location": {"lat": lat, "lng": lon}}

    @staticmethod
    def _fb():
        return settings.SRTM_FALLBACK_ELEV_M

    def test_short_response_does_not_shift_every_waypoint(self):
        """接口少返回中间那条时，只有那个点走兜底，前后两个点必须保持原位。"""
        wps = [Point(lat=29.65, lon=91.13), Point(lat=29.66, lon=91.14),
               Point(lat=29.67, lon=91.15)]
        self._install([{"results": [self._res(29.65, 91.13, 100.0),
                                    self._res(29.67, 91.15, 300.0)]}])
        self.assertEqual(main._fetch_elevations(wps), [100.0, self._fb(), 300.0])

    def test_missing_waypoint_falls_back_not_to_its_neighbour(self):
        """少一条的极端：第一个点没数据，兜底值不许被第二个点的高程顶掉。"""
        wps = [Point(lat=29.65, lon=91.13), Point(lat=29.66, lon=91.14)]
        self._install([{"results": [self._res(29.66, 91.14, 222.0)]}])
        self.assertEqual(main._fetch_elevations(wps), [self._fb(), 222.0])

    def test_duplicate_waypoint_each_gets_its_own_value(self):
        """A→B→A：两个 A 的经维完全相同，都得填上，不能都落到第一个 A 上。"""
        a, b = Point(lat=29.65, lon=91.13), Point(lat=29.66, lon=91.14)
        wps = [a, b, a]
        self._install([{"results": [self._res(29.65, 91.13, 111.0),
                                    self._res(29.66, 91.14, 222.0),
                                    self._res(29.65, 91.13, 111.0)]}])
        self.assertEqual(main._fetch_elevations(wps), [111.0, 222.0, 111.0])

    def test_null_elevation_uses_fallback_in_place(self):
        """接口回了这条但 elevation 是 null：该点兜底，且算作「已配对」，
        否则它会继续参与后面的匹配，把别人的结果抢走。"""
        wps = [Point(lat=29.65, lon=91.13), Point(lat=29.65, lon=91.13)]
        self._install([{"results": [self._res(29.65, 91.13, None),
                                    self._res(29.65, 91.13, 111.0)]}])
        self.assertEqual(main._fetch_elevations(wps), [self._fb(), 111.0])

    def test_second_chunk_failure_keeps_the_first(self):
        """第二个 chunk 挂掉：第一个 chunk 已经取到的高程要留住。"""
        wps = [Point(lat=29.0 + i * 0.01, lon=91.0) for i in range(150)]

        def _fake_urlopen(req, timeout=None):
            url = req.full_url
            # 第二块的第一个航点是纬度 30.0（第一块是 29.00~29.99）
            if "30.0,91.0" in url:
                raise OSError("connection reset")
            locs = [tuple(float(x) for x in pair.split(","))
                    for pair in url.split("locations=")[1].split("|")]
            return type("C", (), {
                "__enter__": lambda s: s, "__exit__": lambda s, *a: False,
                "read": lambda s: json.dumps(
                    {"results": [{"elevation": 1000.0 + i,
                                  "location": {"lat": la, "lng": lo}}
                                 for i, (la, lo) in enumerate(locs)]}
                ).encode(),
            })()

        main.urllib.request.urlopen = _fake_urlopen
        out = main._fetch_elevations(wps)
        self.assertEqual(out[0], 1000.0)          # 第一块保住了
        self.assertEqual(out[99], 1099.0)
        self.assertEqual(out[100], self._fb())     # 第二块整体兜底
        self.assertEqual(len(out), 150)


    def test_turbulence_unavailable_keeps_route_and_serialises_as_null(self):
        """湍流取不到数时的响应形状，这条现在是有承重的。

        两个事实被 plan_route 直接依赖，pydantic 升级时它们会先变：
          1. NaN 序列化成 **JSON null**（合法 JSON），不是裸 NaN 字面量；
          2. verdict 是 undetermined，不是 infeasible。
        另外航线必须**保留** —— A* 每一格都已按硬阈值判过，丢的只是峰值统计。
        """
        import json as _json
        from schemas import (PlanResponse, PLANNER_REASON_VERDICT,
                             VERDICT_UNDETERMINED, verdict_of)

        resp = PlanResponse(
            path=[Point(lat=29.65, lon=91.13), Point(lat=29.66, lon=94.36)],
            total_distance=312.3, max_tke=float('nan'), max_edr=float('nan'),
            feasible=False, reason='turbulence_unavailable')

        payload = _json.loads(resp.model_dump_json())   # 裸 NaN 会在这里抛
        self.assertIsNone(payload['max_tke'])
        self.assertIsNone(payload['max_edr'])
        self.assertEqual(len(payload['path']), 2, "已搜到的航线不能被丢掉")
        self.assertEqual(payload['total_distance'], 312.3)
        self.assertEqual(verdict_of(resp.feasible, resp.reason, PLANNER_REASON_VERDICT),
                         VERDICT_UNDETERMINED)


if __name__ == '__main__':
    unittest.main()
