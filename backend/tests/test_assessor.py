"""AI 辅助评估（assessor）单元测试：上下文构建、规则模板渲染、流式事件序列与降级。"""
import io
import json
import sys
import os
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import assessor  # noqa: E402
from config import settings  # noqa: E402


FEAS_PAYLOAD = {
    "feasible": True,
    "conclusion": "可飞",
    "verdict": "feasible",
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
    "verdict": "infeasible",
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

# 「有安全航线但超预算」与「没算完」：这两档的报告不得写成「飞不了」
OVER_BUDGET_PAYLOAD = dict(
    BLOCKED_PAYLOAD,
    conclusion="超预算", verdict="over_budget", reason="detour_exceeds_threshold",
    blocking={"regions": []},
    distance={"straight_km": 312.11, "route_km": 640.0, "detour_ratio": 2.05,
              "flight_time_h": 10.7,
              "time_cost_h": {"applicable": True, "extra": 5.4, "limit": 2.6,
                              "limit_ratio": 1.5, "exceeded": True}},
)
UNDETERMINED_PAYLOAD = dict(
    BLOCKED_PAYLOAD,
    conclusion="未算出", verdict="undetermined", reason="search_iteration_limit",
    blocking={"regions": []}, risk=None,
    search_effort={"expansions": 20065, "budget": 20065},
)

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
    "meta": {"tz_offset_hours": 8, "data_label_is_utc": True, "scan_step_hours": 1},
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

    def test_context_drops_internal_utc_flag(self):
        """内部时区标志会诱导模型自行换算，不应出现在给 LLM 的上下文里。"""
        ctx = assessor.build_time_window_context(TW_PAYLOAD)
        self.assertNotIn("data_label_is_utc", ctx["meta"])
        self.assertEqual(ctx["meta"]["tz_offset_hours"], 8)
        self.assertEqual(ctx["meta"]["scan_step_hours"], 1)

    def test_context_declares_beijing_time_base(self):
        for ctx in (assessor.build_time_window_context(TW_PAYLOAD),
                    assessor.build_feasibility_context(FEAS_PAYLOAD)):
            self.assertIn("北京时间", ctx["time_base"])
            self.assertIn("禁止", ctx["time_base"])

    def test_prompt_states_time_base_so_model_does_not_shift_hours(self):
        prompt = assessor.build_prompt(assessor.build_time_window_context(TW_PAYLOAD))
        self.assertIn("北京时间", prompt)

    def test_prompt_states_turbulence_calibration(self):
        """换口径后裸数字有歧义：报告必须带上单位与窗口，LLM 版同理。"""
        for ctx in (assessor.build_time_window_context(TW_PAYLOAD),
                    assessor.build_feasibility_context(FEAS_PAYLOAD)):
            note = ctx["turbulence_calibration"]
            self.assertIn("m²/s²", note)
            self.assertIn("m^(2/3)/s", note)
            self.assertIn(f"±{int(settings.TURB_WINDOW_HALF_HOURS)} 小时", note)
        prompt = assessor.build_prompt(assessor.build_time_window_context(TW_PAYLOAD))
        self.assertIn("m²/s²", prompt)

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

    def test_over_budget_report_does_not_say_unflyable(self):
        """绕行超阈是成本不达标，报告印成「不可飞」会让三档对比被读反。"""
        text = assessor.render_rule_report(assessor.build_feasibility_context(OVER_BUDGET_PAYLOAD))
        self.assertIn("「超预算」", text)
        self.assertNotIn("「不可飞」", text)
        self.assertIn("安全航线本身已经找到", text)

    def test_undetermined_report_states_search_did_not_converge(self):
        text = assessor.render_rule_report(assessor.build_feasibility_context(UNDETERMINED_PAYLOAD))
        self.assertIn("「未算出」", text)
        self.assertIn("未收敛", text)
        # 报告也要给出规模：没算完 ≠ 没有航线，读者得能自己判断
        self.assertIn("20065", text)
        self.assertIn("用满了 20065 次", text)

    def test_undetermined_by_clock_says_time_limit_not_full_budget(self):
        """墙钟先到时报告不能说「用满预算」——12 万次预算明明只花了三分之一。"""
        payload = dict(UNDETERMINED_PAYLOAD, search_effort={
            "expansions": 41000, "budget": 120000, "elapsed_s": 18.2})
        text = assessor.render_rule_report(assessor.build_feasibility_context(payload))
        self.assertIn("并未用满", text)
        self.assertIn("18 秒", text)
        self.assertNotIn("用满了 120000 次", text)

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

    def test_failed_llm_does_not_announce_llm_source(self):
        """降级时不得先声明 meta:llm，否则前端来源标签会闪变（重复展示）。"""
        settings.AI_ENABLED = True
        settings.AI_BASE_URL = "http://127.0.0.1:9/v1"
        ctx = assessor.build_feasibility_context(FEAS_PAYLOAD)
        metas = [e for e in assessor.generate_report_stream(ctx) if e["type"] == "meta"]
        self.assertEqual([m["source"] for m in metas], ["rule"])

    def test_partial_llm_output_is_not_followed_by_rule_report(self):
        """LLM 已吐出正文后中断：只补 error，不得再叠一份规则版。"""
        settings.AI_ENABLED = True
        settings.AI_BASE_URL = "http://127.0.0.1:9/v1"
        original = assessor.stream_llm_report

        def _partial(_ctx):
            yield "## 结论概述\n"
            raise urllib.error.URLError("connection reset")

        assessor.stream_llm_report = _partial
        try:
            ctx = assessor.build_feasibility_context(FEAS_PAYLOAD)
            events = list(assessor.generate_report_stream(ctx))
        finally:
            assessor.stream_llm_report = original

        sources = [e["source"] for e in events if e["type"] == "meta"]
        self.assertEqual(sources, ["llm"], "已经产出正文就不该再声明规则版来源")
        body = "".join(e["text"] for e in events if e["type"] == "delta")
        self.assertEqual(body, "## 结论概述\n", "不得追加规则版全文")
        self.assertEqual(events[-1]["type"], "done")

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


def _delta(content):
    """构造一条 OpenAI 兼容的 SSE 正文增量行。"""
    payload = json.dumps({"choices": [{"delta": {"content": content}}]}, ensure_ascii=False)
    return f"data: {payload}\n".encode("utf-8")


def _delta_field(field, content):
    """构造一条带指定 delta 字段（如 reasoning_content）的 SSE 行。"""
    payload = json.dumps({"choices": [{"delta": {field: content}}]}, ensure_ascii=False)
    return f"data: {payload}\n".encode("utf-8")


class _FakeResp(list):
    """可当上下文管理器使用的假响应（模拟 urlopen 的返回对象）。"""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestThinkingControl(unittest.TestCase):
    """推理型模型（MiniMax-M3 等）的思考过程不得混进报告正文。"""

    def setUp(self):
        self._thinking = settings.AI_THINKING
        self._strip = settings.AI_STRIP_THINKING

    def tearDown(self):
        settings.AI_THINKING = self._thinking
        settings.AI_STRIP_THINKING = self._strip

    # ---- 请求侧：关闭思考 ----

    def test_request_body_disables_thinking(self):
        settings.AI_THINKING = "disabled"
        body = assessor.build_request_body({"task": "feasibility"})
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertTrue(body["stream"])

    def test_request_body_supports_adaptive(self):
        settings.AI_THINKING = "adaptive"
        body = assessor.build_request_body({"task": "feasibility"})
        self.assertEqual(body["thinking"], {"type": "adaptive"})

    def test_request_body_omits_thinking_when_blank(self):
        """留空表示不发送该字段，兼容不认识它的网关（Ollama / vLLM）。"""
        settings.AI_THINKING = ""
        self.assertNotIn("thinking", assessor.build_request_body({"task": "feasibility"}))

    def test_request_body_ignores_unknown_value(self):
        settings.AI_THINKING = "sometimes"
        self.assertNotIn("thinking", assessor.build_request_body({"task": "feasibility"}))

    def test_gateway_rejecting_thinking_param_is_retried(self):
        """网关因 thinking 参数返回 400 时，去掉该字段重试而不是整体降级。"""
        calls = []

        def _fake_open(body):
            calls.append(dict(body))
            if "thinking" in body:
                raise urllib.error.HTTPError("http://x", 400, "Bad Request", {}, None)
            return _FakeResp([_delta("正文"), b"data: [DONE]\n"])

        original = assessor._open_stream
        settings.AI_THINKING = "disabled"
        assessor._open_stream = _fake_open
        try:
            text = "".join(assessor.stream_llm_report({"task": "feasibility"}))
        finally:
            assessor._open_stream = original

        self.assertEqual(text, "正文")
        self.assertEqual(len(calls), 2)
        self.assertIn("thinking", calls[0])
        self.assertNotIn("thinking", calls[1])

    def test_other_http_errors_are_not_retried(self):
        """401 之类的鉴权错误重试没有意义，应原样抛出走降级路径。"""
        calls = []

        def _fake_open(body):
            calls.append(dict(body))
            raise urllib.error.HTTPError("http://x", 401, "Unauthorized", {}, None)

        original = assessor._open_stream
        settings.AI_THINKING = "disabled"
        assessor._open_stream = _fake_open
        try:
            with self.assertRaises(urllib.error.HTTPError):
                list(assessor.stream_llm_report({"task": "feasibility"}))
        finally:
            assessor._open_stream = original

        self.assertEqual(len(calls), 1)

    # ---- 响应侧：兜底过滤 ----

    def test_sse_parser_ignores_reasoning_field(self):
        lines = [
            _delta_field("reasoning_content", "让我先想想"),
            _delta("结论：可飞"),
            b"data: [DONE]\n",
        ]
        self.assertEqual("".join(assessor._iter_sse_content(lines)), "结论：可飞")

    def test_sse_parser_strips_inline_thinking_tags(self):
        lines = [
            _delta("<think>" + "好的，我来分析先想想" + "</think>"),
            _delta("## 结论概述"),
            b"data: [DONE]\n",
        ]
        self.assertEqual("".join(assessor._iter_sse_content(lines)), "## 结论概述")

    def test_tag_filter_handles_tag_split_across_chunks(self):
        """标签被 SSE 分块切断时也必须完整剥离。"""
        filt = assessor.ThinkTagFilter()
        pieces = ["前言", "<thi", "nk>", "独白", "</thi", "nk>", "正文"]
        out = "".join(filt.feed(piece) for piece in pieces) + filt.flush()
        self.assertEqual(out, "前言正文")

    def test_tag_filter_drops_unclosed_thinking_tail(self):
        """思考块没有闭合标签时，残稿一律不展示（宁缺毋滥）。"""
        filt = assessor.ThinkTagFilter()
        out = filt.feed("结论如下" + "<think>" + "还没想完")
        out += filt.flush()
        self.assertEqual(out, "结论如下")

    def test_tag_filter_keeps_plain_text(self):
        """不含标签的正文必须逐字保留，包括中文标点与小于号。"""
        filt = assessor.ThinkTagFilter()
        raw = "绕行比 1.326×，TKE < 0.5 的时段占 80%。"
        out = filt.feed(raw) + filt.flush()
        self.assertEqual(out, raw)

    def test_strip_can_be_disabled(self):
        """关掉兜底时正文原样透传（用于排查网关行为）。"""
        settings.AI_STRIP_THINKING = False
        lines = [_delta("keep" + "<think>" + "tail"), b"data: [DONE]\n"]
        self.assertEqual("".join(assessor._iter_sse_content(lines)), "keep" + "<think>" + "tail")

    def test_report_stream_stays_clean_when_gateway_narrates(self):
        """完整链路：网关把思考写进正文时，事件序列仍只有报告内容。"""
        settings.AI_ENABLED = True
        settings.AI_BASE_URL = "http://127.0.0.1:9/v1"
        settings.AI_THINKING = ""  # 模拟不支持 thinking 参数的网关
        lines = [
            _delta("<think>" + "推理独白" + "</think>"),
            _delta("## 结论概述"),
            _delta("：可飞"),
            b"data: [DONE]\n",
        ]

        original = assessor._open_stream
        assessor._open_stream = lambda _body: _FakeResp(lines)
        try:
            events = list(assessor.generate_report_stream(
                assessor.build_feasibility_context(FEAS_PAYLOAD)))
        finally:
            assessor._open_stream = original

        body = "".join(e["text"] for e in events if e["type"] == "delta")
        self.assertEqual(body, "## 结论概述：可飞")
        self.assertEqual(
            [e["source"] for e in events if e["type"] == "meta"], ["llm"])

class TestPromptGuardrails(unittest.TestCase):
    def test_prompt_embeds_structured_json_and_constraints(self):
        ctx = assessor.build_time_window_context(TW_PAYLOAD)
        prompt = assessor.build_prompt(ctx)
        self.assertIn("2022-07-01", prompt)
        self.assertIn("json", prompt.lower())
        self.assertIn("只引用", prompt)

    def test_system_prompt_forbids_fabrication(self):
        self.assertIn("禁止编造", settings.AI_SYSTEM_PROMPT)

    def test_system_prompt_forbids_thinking_narration(self):
        """提示词层面也明确要求不要输出思考旁白。"""
        self.assertIn("不要输出思考过程", settings.AI_SYSTEM_PROMPT)

    def test_system_prompt_forbids_timezone_reconversion(self):
        """正文时间已是北京时间，提示词需禁止模型再换算一次。"""
        self.assertIn("禁止再做时区换算", settings.AI_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
