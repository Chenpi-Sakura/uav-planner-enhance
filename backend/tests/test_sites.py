r"""站点自动规划的行为契约（合成站表 + 替身 A\*，不读真实 NetCDF）。

要守住的口径：
  1. 载重是**自变量**：超商载上限与超公开锚点是两个不同结论——后者判「未算出」，
     因为"没依据说它能飞"和"确定飞不了"在答辩上是两回事；
  2. 场地海拔不参与判定（2026-09-28 裁定）：站表的 elevation_m / elev_kind 只作展示与
     复核台账，两台机型拿到的站点池必须相同；
  3. 目标函数是字典序（跳数 → 直线里程 → 气象），落地次数优先于总里程；
  4. 一次请求只读一次场（Pass 2 与 Pass 3 共用同一份）——这是 <6 s 预算成立的前提，
     只测编排层等于没测；双机对照已于 2026-09-28 撤销，两台的差异改用两次单机请求对照；
  5. 段数上限或墙钟封顶时返回部分结果并标注「未复验」，不许回空数组装作无解；
  6. 自然语言兜底解析不许发明参数：认不出的字段留空，编一个载重就是拿演示骗自己。
"""
import os
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sites  # noqa: E402
from schemas import (Point, SitePlanOption, SitePlanRequest, SitePlanResponse,  # noqa: E402
                     TaskParseRequest, VERDICT_INFEASIBLE, VERDICT_UNDETERMINED)

# 1° 经度在 29.6°N 上约 96.6 km，下面所有几何都按这个尺度摆
LAT = 29.6
DEG_LON_KM = 96.6


def _site(sid, name, lon, elev, kind="official_published", conflict=False, vp=0,
          lat=LAT, usable=True):
    return {"id": sid, "name": name, "type": "county", "region": "测试-合成",
            "lat": round(lat, 4), "lon": round(lon, 4), "elevation_m": elev,
            "elev_kind": kind, "elev_conflict": conflict, "geodatum": "WGS84",
            "hajin": elev >= 2438, "usable": usable, "verify_priority": vp,
            "source": "合成", "note": "合成"}


def _doc(*rows):
    return {"sites": list(rows), "stats": {"total": len(rows),
                                           "usable": sum(1 for r in rows if r["usable"])}}


def _req(start, end, **kw):
    kw.setdefault("date", "2022-07-01")
    kw.setdefault("start_time", "08:00")
    # 不给 speed：这才是线上默认（每台按能力包的巡航速度推时间轴）
    return SitePlanRequest(start=start, end=end, **kw)


def _fake_plan(req, field=None, deadline=None):
    """替身 A*：出一条「直线 × 1.08」的线，风险全为低。几何真实、代价不真实，够用。"""
    d = sites._haversine_km(req.start.lat, req.start.lon, req.end.lat, req.end.lon)
    path = [Point(lat=req.start.lat, lon=req.start.lon),
            Point(lat=req.end.lat, lon=req.end.lon)]

    class _R:
        found = True
        reason = None
        path = None
        total_distance = 0.0
        tkes: list = []
        edrs: list = []
        times: list = []

    r = _R()
    r.path = path
    r.total_distance = d * 1.08
    r.tkes = [3.0] * len(path)
    r.edrs = [0.05] * len(path)
    return r


class _StubLoader:
    """替身 data_loader：只数读了几次盘，并给一块平坦的低湍流场。"""

    def __init__(self, value=3.0):
        self.calls = 0
        self.value = value

    def read_turbulence_field(self, start_time, hours, bbox):
        self.calls += 1
        lat0, lat1, lon0, lon1 = bbox
        lats = np.arange(lat0, lat1, 0.05)
        lons = np.arange(lon0, lon1, 0.05)
        shape = (int(hours), lats.size, lons.size)
        tke = np.full(shape, self.value, dtype=float)
        edr = np.full(shape, 0.05, dtype=float)
        times = (np.arange(int(hours)) * np.timedelta64(1, 'h')
                 + np.datetime64('2022-06-30T00:00'))
        return tke, edr, lats, lons, times


class _Base(unittest.TestCase):
    def setUp(self):
        self._orig_plan = sites.plan_path_ex
        self._orig_loader = sites.data_loader
        self._orig_effort = sites.search_effort_of
        self._orig_doc = getattr(sites, "load_sites_doc", None)
        self._orig_deadline = sites.search_deadline
        self._orig_max_legs = sites.settings.SITE_MAX_LEGS
        sites.plan_path_ex = _fake_plan
        sites.search_effort_of = lambda p: None
        sites.data_loader = _StubLoader()

    def tearDown(self):
        sites.plan_path_ex = self._orig_plan
        sites.data_loader = self._orig_loader
        sites.search_effort_of = self._orig_effort
        sites.search_deadline = self._orig_deadline
        sites.settings.SITE_MAX_LEGS = self._orig_max_legs
        if self._orig_doc is not None:
            sites.load_sites_doc = self._orig_doc

    def use_doc(self, doc):
        sites.load_sites_doc = lambda *a, **k: doc


class TestLegLimit(unittest.TestCase):
    def test_td550_cargo_and_crew_margins(self):
        cargo = sites.leg_limit("td550", 200.0, "cargo")
        crew = sites.leg_limit("td550", 200.0, "crew")
        self.assertEqual(cargo["leg_limit_km"], 160.0)   # 200 km 任务半径 × 0.8
        self.assertEqual(crew["leg_limit_km"], 120.0)    # 载人余度收紧到 0.6

    def test_oil_endurance_is_capped_by_operational_radius(self):
        """100 kg 档能源能飞 650 km，但厂家「任务半径 200 km」封顶——两栏都要如实。"""
        light = sites.leg_limit("td550", 100.0, "cargo")
        self.assertEqual(light["usable_km"], 200.0)
        self.assertEqual(light["leg_limit_km"], 160.0)

    def test_electric_only_has_a_200kg_anchor(self):
        self.assertEqual(sites.leg_limit("v2000cg", 200.0, "cargo")["leg_limit_km"], 160.0)
        self.assertEqual(sites.leg_limit("v2000cg", 120.0, "cargo")["leg_limit_km"], 160.0)
        # 轻载不向下外推：没给 0 kg 的航程，就沿用 200 kg 锚点值
        self.assertEqual(sites.leg_limit("v2000cg", 10.0, "cargo")["usable_km"], 200.0)


class TestPayloadGates(_Base):
    def test_over_max_payload_is_infeasible(self):
        self.use_doc(_doc(_site("CT-1", "甲", 90.5, 2500)))
        req = _req(Point(lat=LAT, lon=90.0), Point(lat=LAT, lon=92.0),
                   aircraft="td550", payload_kg=300.0)
        opt = sites.plan_site_options(req)["options"][0]
        self.assertEqual(opt["reason"], "payload_exceeds_max")
        self.assertEqual(opt["verdict"], VERDICT_INFEASIBLE)
        self.assertIn("260", opt["message"])

    def test_beyond_published_envelope_is_undetermined_not_infeasible(self):
        """300 kg 在 V2000CG 的 400 kg 商载内，但公开锚点只到 200 kg：判「没依据」。"""
        self.use_doc(_doc(_site("CT-1", "甲", 90.5, 2500)))
        req = _req(Point(lat=LAT, lon=90.0), Point(lat=LAT, lon=92.0),
                   aircraft="v2000cg", payload_kg=300.0)
        opt = sites.plan_site_options(req)["options"][0]
        self.assertEqual(opt["reason"], "payload_beyond_published_envelope")
        self.assertEqual(opt["verdict"], VERDICT_UNDETERMINED)
        self.assertIn("不作答", opt["message"])

    def test_default_payload_is_the_evidence_ceiling(self):
        req = _req(Point(lat=LAT, lon=90.0), Point(lat=LAT, lon=92.0), aircraft="td550")
        out = sites.plan_site_options(req)
        self.assertEqual(out["meta"]["payload_kg"],
                         sites.settings.SITE_DEFAULT_PAYLOAD_KG)
        self.assertEqual(out["meta"]["payload_kg"], 200.0)


class TestGraphObjective(_Base):
    def test_fewer_landings_wins_over_shorter_distance(self):
        """直达 125 km（2 跳 121 km 更短）也要选直达：落地次数是用户真正付的代价。"""
        self.use_doc(_doc(_site("CT-1", "近甲", 90.65, 2500),
                          _site("CT-2", "近乙", 91.35, 2500)))
        req = _req(Point(lat=LAT, lon=90.0), Point(lat=LAT, lon=91.3), aircraft="td550")
        opt = sites.plan_site_options(req)["options"][0]
        self.assertTrue(opt["feasible"], opt["message"])
        self.assertEqual(opt["relay_count"], 0)
        self.assertEqual(len(opt["legs"]), 1)

    def test_relays_are_inserted_when_leg_too_long(self):
        row = [_site(f"CT-{i}", f"站{i}", 90.7 + 0.6 * i, 2500) for i in range(4)]
        self.use_doc(_doc(*row))
        req = _req(Point(lat=LAT, lon=90.0), Point(lat=LAT, lon=93.2), aircraft="td550")
        opt = sites.plan_site_options(req)["options"][0]
        self.assertTrue(opt["feasible"], opt["message"])
        self.assertGreaterEqual(opt["relay_count"], 2)
        self.assertEqual(len(opt["legs"]), opt["relay_count"] + 1)
        # 每段直线都必须在建图段长（段长上限折掉绕行余量）之内
        for leg in opt["legs"]:
            self.assertLessEqual(leg["straight_km"], opt["diagnostics"]["edge_cap_km"] + 0.1)

    def test_total_distance_uses_route_km_not_straight(self):
        """回归：曾把已复验段的里程加成 0，总里程只剩未复验段。"""
        row = [_site(f"CT-{i}", f"站{i}", 90.7 + 0.6 * i, 2500) for i in range(4)]
        self.use_doc(_doc(*row))
        req = _req(Point(lat=LAT, lon=90.0), Point(lat=LAT, lon=93.2), aircraft="td550")
        opt = sites.plan_site_options(req)["options"][0]
        straight = sum(l["straight_km"] for l in opt["legs"])
        self.assertGreater(opt["total_distance"], straight * 1.02)

    def test_unreachable_reports_the_gap_not_an_empty_answer(self):
        self.use_doc(_doc(_site("CT-1", "孤站", 91.0, 2500)))
        req = _req(Point(lat=LAT, lon=90.0), Point(lat=LAT, lon=93.0), aircraft="td550")
        opt = sites.plan_site_options(req)["options"][0]
        self.assertEqual(opt["reason"], "network_unreachable")
        d = opt["diagnostics"]
        self.assertEqual(d["component_size"], 2)          # 起点 + 孤站
        self.assertGreater(d["gap_km"], 0.0)
        self.assertTrue(d["frontier_site"])

    def test_dedup_close_keeps_the_higher_site(self):
        """西宁城东区/城中区相距 1.14 km 的真实例子：收敛成一个，留高不留低。"""
        kept = sites._dedup_close([_site("CT-1", "城东区", 101.75, 2236),
                                   _site("CT-2", "城中区", 101.76, 2251)],
                                  sites.settings.SITE_MIN_SPACING_KM)
        self.assertEqual([s["id"] for s in kept], ["CT-2"])


class TestFieldAndBudget(_Base):
    def test_one_request_reads_the_field_once(self):
        """一次请求只读一次盘：Pass 2 邻域统计与 Pass 3 逐段复验共用同一份场。"""
        row = [_site(f"CT-{i}", f"站{i}", 90.7 + 0.6 * i, 2500) for i in range(4)]
        self.use_doc(_doc(*row))
        req = _req(Point(lat=LAT, lon=90.0), Point(lat=LAT, lon=93.2))   # 不给 aircraft
        out = sites.plan_site_options(req)
        self.assertEqual(len(out["options"]), 1)                        # 一台一请求
        self.assertEqual(out["meta"]["aircraft"], sites.settings.SITE_DEFAULT_AIRCRAFT)
        self.assertEqual(sites.data_loader.calls, 1)

    def test_max_legs_marks_the_rest_as_unverified(self):
        sites.settings.SITE_MAX_LEGS = 1
        row = [_site(f"CT-{i}", f"站{i}", 90.7 + 0.6 * i, 2500) for i in range(4)]
        self.use_doc(_doc(*row))
        req = _req(Point(lat=LAT, lon=90.0), Point(lat=LAT, lon=93.2), aircraft="td550")
        opt = sites.plan_site_options(req)["options"][0]
        unverified = [l for l in opt["legs"] if not l["reverified"]]
        self.assertTrue(unverified)
        self.assertIn("段数上限", unverified[0]["note"])
        self.assertTrue(any("未做 A* 复验" in n for n in opt["notes"]))

    def test_wall_clock_exhausted_still_returns_partial(self):
        row = [_site(f"CT-{i}", f"站{i}", 90.7 + 0.6 * i, 2500) for i in range(4)]
        self.use_doc(_doc(*row))
        req = _req(Point(lat=LAT, lon=90.0), Point(lat=LAT, lon=93.2), aircraft="td550")
        sites.settings.SITE_MAX_LEGS = 99
        import time as _t
        expired = _t.monotonic() - 1.0
        orig = sites.plan_path_ex
        calls = {"n": 0}

        def _plan(req2, field=None, deadline=None):
            calls["n"] += 1
            return orig(req2, field=field, deadline=deadline)

        sites.plan_path_ex = _plan
        # 墙钟在进 Pass 3 之前就已耗尽：一段都不复验，但结论仍要给出序列与诊断
        sites.search_deadline = lambda seconds=None: expired
        try:
            out = sites.plan_site_options(req)
        finally:
            sites.search_deadline = self._orig_deadline
        opt = out["options"][0]
        self.assertTrue(opt["legs"])
        self.assertTrue(all(not l["reverified"] for l in opt["legs"]))

    def test_over_range_leg_rejects_with_detour_reason(self):
        def _hot(req, field=None, deadline=None):
            d = sites._haversine_km(req.start.lat, req.start.lon, req.end.lat, req.end.lon)

            class _R:
                found = True
                reason = None
                total_distance = d * 1.5
                tkes = [3.0, 3.0]
                edrs = [0.05, 0.05]
                times = []
                path = [Point(lat=req.start.lat, lon=req.start.lon),
                        Point(lat=req.end.lat, lon=req.end.lon)]

            return _R()

        sites.plan_path_ex = _hot
        row = [_site(f"CT-{i}", f"站{i}", 90.7 + 0.6 * i, 2500) for i in range(4)]
        self.use_doc(_doc(*row))
        req = _req(Point(lat=LAT, lon=90.0), Point(lat=LAT, lon=93.2), aircraft="td550")
        opt = sites.plan_site_options(req)["options"][0]
        self.assertEqual(opt["reason"], "detour_exceeds_threshold")
        self.assertIn("over_range_legs", opt["diagnostics"])


class TestCruiseSpeed(_Base):
    """巡航速度按机型取：距离是「航时 × 巡航速度」折出来的，时间轴不能用另一把尺。"""

    def _corridor(self):
        row = [_site(f"CT-{i}", f"站{i}", 90.7 + 0.6 * i, 2500) for i in range(4)]
        self.use_doc(_doc(*row))
        return _req(Point(lat=LAT, lon=90.0), Point(lat=LAT, lon=93.2))

    def _run(self, pack_key, **kw):
        req = self._corridor()
        req.aircraft = pack_key
        for k, v in kw.items():
            setattr(req, k, v)
        return sites.plan_site_options(req)["options"][0]

    def test_each_aircraft_uses_its_own_cruise_speed(self):
        # 双机对照下线之后，"两台各用自己的速度"要靠两次单机请求对照出来
        a = self._run("td550")
        b = self._run("v2000cg")
        self.assertTrue(a["feasible"] and b["feasible"], (a["message"], b["message"]))
        self.assertEqual(a["total_distance"], b["total_distance"])   # 同一条线
        # 同一条线，纯电按 161 km/h 推的时间要比油动按 100 km/h 少约四成
        self.assertAlmostEqual(b["total_time_h"] / a["total_time_h"], 100.0 / 161.0, places=2)

    def test_explicit_speed_overrides_the_pack(self):
        one = self._run("td550", speed=60.0)
        self.assertAlmostEqual(one["total_time_h"], one["total_distance"] / 60.0, places=2)
        self.assertEqual(sites.data_loader.calls, 1)

    def test_pack_speeds_are_the_same_ruler_as_the_range_anchors(self):
        """油动锚点按 100 km/h 折公里，巡航速度就必须是 100，否则两处口径分叉。"""
        pack = sites.AIRCRAFT_PACKS["td550"]
        self.assertEqual(pack["cruise_kmh"], 100.0)
        self.assertEqual(pack["range_anchors"][0], (100.0, 650.0))   # 6.5 h × 100 = 650 km
        ev = sites.AIRCRAFT_PACKS["v2000cg"]
        self.assertTrue(ev["cruise_note"].startswith("换算值"))       # 厂家未公布，必须标换算


class TestResponseShape(_Base):
    def test_option_dict_validates_as_model(self):
        self.use_doc(_doc(_site("CT-1", "甲", 90.6, 2500), _site("CT-2", "乙", 91.2, 2500)))
        req = _req(Point(lat=LAT, lon=90.0), Point(lat=LAT, lon=91.8), aircraft="td550")
        out = sites.plan_site_options(req)
        resp = SitePlanResponse(**out)
        self.assertIsInstance(resp.options[0], SitePlanOption)
        self.assertEqual(resp.meta["payload_kg"], 200.0)

    def test_search_effort_survives_validation(self):
        self.use_doc(_doc(_site("CT-1", "甲", 90.6, 2500)))
        req = _req(Point(lat=LAT, lon=90.0), Point(lat=LAT, lon=91.0), aircraft="td550")
        sites.search_effort_of = lambda p: {"expansions": 12, "budget": 34, "elapsed_s": 0.2}
        try:
            out = sites.plan_site_options(req)
        finally:
            sites.search_effort_of = lambda p: None
        opt = SitePlanResponse(**out).options[0]
        self.assertEqual(opt.search_effort.expansions, 12)


class TestTaskParse(unittest.TestCase):
    def test_rule_path_extracts_weight_mission_and_direction(self):
        got = sites.parse_task(TaskParseRequest(text="从拉萨送200公斤兽药到昌都", use_llm=False))
        self.assertEqual(got["source"], "rule")
        f = got["fields"]
        self.assertEqual(f["payload_kg"], 200.0)
        self.assertEqual(f["mission"], "cargo")
        self.assertEqual((f["start_name"], f["end_name"]), ("拉萨", "昌都"))

    def test_tonnes_convert_and_crew_wins_over_cargo_words(self):
        got = sites.parse_task(TaskParseRequest(text="送1.5吨建材，顺带捎两名乘客", use_llm=False))
        self.assertEqual(got["fields"]["payload_kg"], 1500.0)
        self.assertEqual(got["fields"]["mission"], "crew")

    def test_unknown_text_invents_nothing(self):
        got = sites.parse_task(TaskParseRequest(text="帮我看看明天天气", use_llm=False))
        for key in ("payload_kg", "aircraft", "mission", "start_name", "end_name"):
            self.assertIsNone(got["fields"][key], key)

    def test_aircraft_keyword_maps_to_a_known_pack(self):
        got = sites.parse_task(TaskParseRequest(text="用油动直升机把物资送到山南", use_llm=False))
        self.assertEqual(got["fields"]["aircraft"], "td550")
        self.assertIn(got["fields"]["aircraft"], sites.AIRCRAFT_PACKS)

    def test_llm_output_is_filtered_to_the_whitelist(self):
        """模型多吐的字段（比如它自作主张给的续航）必须被丢掉。"""
        orig = sites._llm_parse
        sites._llm_parse = lambda text: {"payload_kg": 180.0, "aircraft": "td550",
                                         "mission": None, "start_name": "拉萨",
                                         "end_name": "昌都", "endurance_km": 999,
                                         "range_anchors": "瞎编"}
        sites.settings.AI_ENABLED = True
        try:
            got = sites.parse_task(TaskParseRequest(text="从拉萨送货到昌都"))
        finally:
            sites._llm_parse = orig
            sites.settings.AI_ENABLED = False
        self.assertEqual(got["source"], "llm")
        self.assertNotIn("endurance_km", got["fields"])
        self.assertEqual(got["fields"]["payload_kg"], 180.0)
        # LLM 没认出的字段仍由规则版兜着，而不是被 None 覆盖
        self.assertEqual(got["fields"]["mission"], "cargo")


class TestRealSiteTable(unittest.TestCase):
    """对着仓库里真实的 sites.json 跑一遍纯几何层（不读盘、不跑 A*）。"""

    @classmethod
    def setUpClass(cls):
        if not sites.sites_file_path().is_file():
            raise unittest.SkipTest("站表未生成")
        cls.doc = sites.load_sites_doc(force=True)

    def test_table_matches_the_documented_counts(self):
        st = self.doc["stats"]
        self.assertEqual(st["total"], 220)
        self.assertEqual(st["usable"], 210)
        # 9 条被留痕排除：7 条在气象框外 + 2 条机场未建成/状态待核实
        self.assertEqual(st["excluded"], 10)
        self.assertGreater(st["pool_at_3000"], 0)
        self.assertGreater(st["pool_at_5000"], st["pool_at_3000"])
        # 复核过的条目必须留下结构化依据，不能只改数字
        self.assertGreaterEqual(st["verified"], 50)

    def test_delingha_crossed_the_electric_gate_after_review(self):
        """德令哈机场：原表 3001 m（判电机不可用）→ 民航局验收文件 2862.2 m（可用）。

        这条是"海拔复核值得单独立一项"的全部理由：当年一个符号之差决定电机档有没有站可落。
        2026-09-28 之后海拔不再判定，这条留作数据依据，不再是结论依据。
        """
        row = next(s for s in self.doc["sites"] if s["name"] == "德令哈机场")
        self.assertEqual(row["elevation_m"], 2862.2)
        self.assertEqual(row["elev_kind"], "official_published")
        self.assertTrue(row["verified"]["crosses_3000_gate"])

    def test_planned_airports_are_not_usable_stations(self):
        """在建/仅"拟定场址"的机场不能当补给点：留痕在表里，但不进池。"""
        for name in ("共和青海湖机场", "海晏(青海湖)通用机场候选"):
            row = next(s for s in self.doc["sites"] if s["name"] == name)
            self.assertFalse(row["usable"], name)
            self.assertIn(row["exclude_reason"],
                          ("not_operating_planned", "status_unverified"), name)

    def test_every_usable_site_has_elevation_and_crs(self):
        for s in self.doc["sites"]:
            if not s.get("usable"):
                continue
            self.assertIsNotNone(s.get("elevation_m"), s["id"])
            self.assertIn(s.get("geodatum"), ("WGS84", "GCJ02", "unknown"), s["id"])
            self.assertTrue(s.get("source"), s["id"])

    def test_elevation_no_longer_gates_the_pool(self):
        """海拔不筛站，两台机型拿到的池一样，且等于表内 usable 数。"""
        sites.plan_path_ex = _fake_plan
        sites.search_effort_of = lambda p: None
        sites.data_loader = _StubLoader()
        got = {}
        for key in ("td550", "v2000cg"):
            req = _req(Point(lat=29.66, lon=94.36), Point(lat=29.89, lon=93.25),
                       aircraft=key, weather_metrics=False)
            got[key] = sites.plan_site_options(req)["options"][0]["usable_sites"]
        self.assertEqual(got["td550"], got["v2000cg"], "池不该随机型变")
        self.assertEqual(got["td550"], self.doc["stats"]["usable"])


if __name__ == "__main__":
    unittest.main()
