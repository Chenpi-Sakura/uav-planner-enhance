"""AI 辅助评估（assessor）单元测试：上下文构建、规则模板渲染、流式事件序列与降级。"""
import io
import json
import sys
import os
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import assessor  # noqa: E402
from config import settings  # noqa: E402


FEAS_PAYLOAD = {
    "feasible": True,
    "conclusion": "可飞",
    "reason": None,
    "priority": "normal",
    "distance": {
        "straight_km": 312.11,
        "route_km": 312.26,
        "detour_ratio": 1.0,
        "flight_time_h": 5.2,
        "time_cost_h": {
            "applicable": True, "extra": 0.0, "limit": 2.6,
            "limit_ratio": 1.5, "exceeded": False,
        },
    },
    "risk": {
        "overall": "中风险",
        "stats": {"max_tke": 0.4078, "max_edr": 0.0013, "moderate_ratio": 0.029, "n_samples": 70},
        "segments": [{
            "from_name": "拉萨市区附近", "to_name": "墨竹工卡",
            "level": "低风险", "max_tke": 0.1852, "distance_km": 57.98,
        }],
    },
    "blocking": {"regions": []},
    "suggestions": {"alt_bases": []},
    "meta": {"endpoints": {
        "start": {"describe": "拉萨市区附近"},
        "end": {"describe": "林芝以北约21km"},
    }},
}

BLOCKED_PAYLOAD = {
    "feasible": False,
    "conclusion": "不可飞",
    "reason": "no_safe_path",
    "priority": "urgent",
    "distance": {"straight_km": 220.0, "route_km": None, "detour_ratio": None,
                 "flight_time_h": None, "time_cost_h": {"applicable": False}},
    "risk": {"overall": "不可飞", "stats": {"max_tke": 1.35, "max_edr": 0.02,
                                            "moderate_ratio": 0.4, "n_samples": 50},
             "segments": []},
    "blocking": {"regions": [{"shape": "块状", "area_km2": 5300.0, "max_tke": 1.35,
                              "center": [29.9, 93.2]}]},
    "suggestions": {"alt_bases": [{"role": "end", "role_label": "替代终点",
                                   "description": "林芝以东约20km", "level": "低风险",
                                   "distance_km": 20.0}]},
    "meta": {},
}

TW_PAYLOAD = {
    "date": "2022-07-01",
    "has_window": True,
    "recommended": {
        "takeoff": "2022-07-01T05:00",
        "window": ["04:00", "10:00"],
        "landing": "2022-07-01T10:21",
        "level": "低风险",
        "max_tke": 0.0541,
        "max_edr": 0.0001,
    },
    "slot_analysis": [
        {"time": "00:00", "max_tke": 0.19, "safe": True},
        {"time": "12:00", "max_tke": 1.2, "safe": False},
    ],
    "backup_windows": [{"start": "21:00", "end": "23:00", "level": "低风险", "max_tke": 0.2}],
    "warnings": [{"level": "warning", "text": "相对湿度超过 90%，可能存在结冰风险"}],
    "forecast": {"enabled": True, "first_available_date": "2022-07-03",
                 "days": [{"date": "2022-07-02", "has_window": False, "best_window": None,
                           "level": None, "safe_slot_count": 0},
                          {"date": "2022-07-03", "has_window": True,
                           "best_window": ["06:00", "11:00"], "level": "低风险",
                           "safe_slot_count": 8}]},
    "meta": {"tz_offset_hours": 8},
}


class TestContextBuild(unittest.TestCase):
    def test_feasibility_context_extracts_core_fields(self):
        ctx = assessor.build_feasibility_context(FEAS_PAYLOAD)
        self.assertEqual(ctx["task"], "feasibility")
        self.assertTrue(ctx["feasible"])
        self.assertEqual(ctx["risk"]["max_tke"], 0.4078)
        self.assertEqual(ctx["segments"][0]["from"], "拉萨市区附近")
        self.assertEqual(ctx["risk"]["n_samples"], 70)

    def test_time_window_context_counts_safe_slots(self):
        ctx = assessor.build_time_window_context(TW_PAYLOAD)
        self.assertEqual(ctx["task"], "time_window")
        self.assertEqual(ctx["safe_slot_count"], 1)
        self.assertEqual(ctx["forecast"]["first_available_date"], "2022-07-03")

    def test_context_tolerates_empty_payload(self):
        ctx = assessor.build_feasibility_context({})
        self.assertIsNone(ctx["feasible"])
        self.assertEqual(ctx["segments"], [])

    def test_time_window_context_tolerates_empty_payload(self):
        ctx = assessor.build_time_window_context({})
        self.assertEqual(ctx["safe_slot_count"], 0)
        self.assertEqual(ctx["warnings"], [])


class TestRuleReport(unittest.TestCase):
    def test_feasible_report_contains_all_sections(self):
        text = assessor.render_rule_report(assessor.build_feasibility_context(FEAS_PAYLOAD))
        for section in ["结论概述", "航线地理叙事", "气象解读", "时间建议", "操作建议", "数据口径声明"]:
            self.assertIn(section, text)
        self.assertIn("312.26", text)
        self.assertIn("拉萨市区附近", text)

    def test_blocked_report_explains_reason_and_alternatives(self):
        text = assessor.render_rule_report(assessor.build_feasibility_context(BLOCKED_PAYLOAD))
        self.assertIn("不可飞", text)
        self.assertIn("航路被高强度湍流区封死", text)
        self.assertIn("替代终点", text)

    def test_time_window_report_mentions_timezone_and_forecast(self):
        text = assessor.render_rule_report(assessor.build_time_window_context(TW_PAYLOAD))
        self.assertIn("推荐", text)
        self.assertIn("UTC", text)
        self.assertIn("2022-07-03", text)
        self.assertIn("结冰", text)

    def test_no_window_report_states_absence(self):
        payload = dict(TW_PAYLOAD, has_window=False, recommended=None)
        text = assessor.render_rule_report(assessor.build_time_window_context(payload))
        self.assertIn("未找到", text)

    def test_report_never_invents_absent_values(self):
        """空 payload 的报告不应出现臆造数值。"""
        text = assessor.render_rule_report(assessor.build_feasibility_context({}))
        self.assertNotIn("None", text)
        self.assertIn("—", text)


class TestStreaming(unittest.TestCase):
    def setUp(self):
        self._orig_enabled = settings.AI_ENABLED
        self._orig_base = settings.AI_BASE_URL
        settings.AI_ENABLED = False
        settings.AI_BASE_URL = ""

    def tearDown(self):
        settings.AI_ENABLED = self._orig_enabled
        settings.AI_BASE_URL = self._orig_base

    def test_rule_stream_event_sequence(self):
        ctx = assessor.build_feasibility_context(FEAS_PAYLOAD)
        events = list(assessor.generate_report_stream(ctx))
        self.assertEqual(events[0]["type"], "meta")
        self.assertEqual(events[0]["source"], "rule")
        self.assertEqual(events[-1]["type"], "done")
        body = "".join(e["text"] for e in events if e["type"] == "delta")
        self.assertIn("结论概述", body)

    def test_falls_back_to_rule_when_llm_fails(self):
        """LLM 打开但端点不可达时，必须降级到规则版且仍产出完整报告。"""
        settings.AI_ENABLED = True
        settings.AI_BASE_URL = "http://127.0.0.1:9/v1"  # 不可达端口
        ctx = assessor.build_feasibility_context(FEAS_PAYLOAD)
        events = list(assessor.generate_report_stream(ctx))
        types = [e["type"] for e in events]
        self.assertIn("error", types)
        self.assertEqual(events[-1]["type"], "done")
        body = "".join(e["text"] for e in events if e["type"] == "delta")
        self.assertIn("结论概述", body)

    def test_status_reports_disabled_state(self):
        status = assessor.report_available()
        self.assertIn("llm_enabled", status)
        self.assertFalse(status["llm_ready"])
        self.assertNotIn("api_key", json.dumps(status).lower())

    def test_sse_parser_extracts_deltas(self):
        """_iter_sse_content 能解析 OpenAI 兼容的 SSE 增量格式。"""
        lines = [
            b'data: {"choices":[{"delta":{"content":"hello"}}]}\n',
            b"\n",
            b'data: {"choices":[{"delta":{"content":" world"}}]}\n',
            b"data: [DONE]\n",
        ]
        self.assertEqual("".join(assessor._iter_sse_content(lines)), "hello world")

    def test_sse_parser_skips_malformed_lines(self):
        lines = [b": keep-alive\n", b"data: not-json\n", b'data: {"choices":[]}\n']
        self.assertEqual(list(assessor._iter_sse_content(lines)), [])


class TestPromptGuardrails(unittest.TestCase):
    def test_prompt_embeds_structured_json_and_constraints(self):
        ctx = assessor.build_time_window_context(TW_PAYLOAD)
        prompt = assessor.build_prompt(ctx)
        self.assertIn("2022-07-01", prompt)
        self.assertIn("json", prompt.lower())
        self.assertIn("只引用", prompt)

    def test_system_prompt_forbids_fabrication(self):
        self.assertIn("禁止编造", settings.AI_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
