"""后续建议胶囊：去重与「不重复用户刚问过的问题」。

背景（2026-10-04 实测）：`recent` 早就传进提示词了，但那只是「请模型避开」
的建议，不是保证。同一段对话里连续四次生成，第一条胶囊全是「哪段最颠」类，
其中一次与用户刚发出的问句一字不差 —— 用户点它等于把自己的话再问一遍。
提示词层面已经要求模型避开（见 suggest._SUGGEST_SYSTEM 第 9 条），
但**输出侧必须自己拦一道**：模型不听是常态，不是意外。
"""
import os
import sys
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import suggest


class TestNorm(unittest.TestCase):
    def test_punctuation_and_width_do_not_block_comparison(self):
        """标点与全半角差异不能让「同一个问题」看起来像两个。"""
        self.assertEqual(
            suggest._norm("哪一段的颠簸最厉害?"),
            suggest._norm("哪一段的颠簸最厉害？"),
        )

    def test_empty_input_is_safe(self):
        self.assertEqual(suggest._norm(""), "")
        self.assertEqual(suggest._norm(None), "")


class TestDedup(unittest.TestCase):
    def _chip(self, label, prompt):
        return {"label": label, "prompt": prompt}

    def test_drops_chip_the_user_just_asked(self):
        """用户刚发过的那句，原样弹回来 —— 这就是用户看到的「出现了两次」。"""
        recent = ["拉萨到林芝这条航线，哪一段的颠簸最厉害?"]
        chips = [
            self._chip("哪段最颠", "拉萨到林芝这条航线，哪一段的颠簸最厉害?"),
            self._chip("换个时间", "换个时间飞会不会好一些？"),
        ]
        got = suggest._dedup(chips, recent)
        self.assertEqual([c["label"] for c in got], ["换个时间"])

    def test_drops_shorter_restatement_containing_the_question(self):
        """短句被长句包住也算重复：「最颠的数值」问的是同一件事。

        判据是**归一化后的包含关系**，不是语义相似 —— 「哪段最颠」与
        「哪一段的颠簸最厉害」说的是同一件事，但字面上互不包含，纯文本比对
        （子串、二元组都试过）抓不到它。这种改写只能靠提示词约束第 9 条，
        漏掉的后果是用户看到一条略啰嗦的建议，不是错误结论，可以接受。
        """
        recent = ["帮我看看 TKE 最大能到多少，出现在哪一段？"]
        chips = [self._chip("最颠的数值", "TKE 最大能到多少，出现在哪一段")]
        self.assertEqual(suggest._dedup(chips, recent), [])

    def test_paraphrase_may_still_slip_through_by_design(self):
        """改写过的同一问题可能漏过去 —— 这是**已知边界**，钉住它以免被当成回归。

        抓它需要语义比对，而语义比对会误杀合法建议（宁可少一条也别错删）。
        实测漏过去的后果：用户看到一条和刚问过的高度相似的建议，
        点下去得到一个正确但重复的答案。远好过给错结论。
        """
        recent = ["拉萨到林芝这条航线，哪一段的颠簸最厉害?"]
        chips = [self._chip("颠簸最强在哪段", "这条线上最颠的地方是哪里？")]
        self.assertEqual(len(suggest._dedup(chips, recent)), 1,
                         "语义改写当前抓不到，这是有意的取舍，不是 bug")

    def test_drops_duplicate_inside_one_batch(self):
        """模型偶尔把同一个问题写两遍，只留第一条。"""
        chips = [
            self._chip("A", "中风险是哪来的？"),
            self._chip("B", "中风险是哪来的"),
            self._chip("C", "换个高度飞会怎样？"),
        ]
        got = suggest._dedup(chips, [])
        self.assertEqual([c["label"] for c in got], ["A", "C"])

    def test_keeps_genuinely_different_chips(self):
        """别把去重做成「只留一条」—— 不同角度的问题必须都留。"""
        chips = [
            self._chip("哪段最颠", "哪一段的颠簸最厉害？"),
            self._chip("几点飞稳", "哪个时段起飞颠簸最小？"),
            self._chip("能飞吗", "这条航线到底能不能飞？"),
        ]
        self.assertEqual(len(suggest._dedup(chips, [])), 3)

    def test_no_recent_still_dedups_within_batch(self):
        """没有历史时也要能挡批次内重复。"""
        chips = [self._chip("A", "同样的问题？"), self._chip("B", "同样的问题")]
        self.assertEqual(len(suggest._dedup(chips, [])), 1)

    def test_empty_prompt_is_dropped(self):
        self.assertEqual(suggest._dedup([self._chip("X", "")], []), [])


class TestRuleFallbackDedup(unittest.TestCase):
    def test_rule_chips_also_respect_avoid_list(self):
        """规则兜底里的固定句式（「哪一段最危险」）同样不能对刚问过的话弹回来。

        去重放在 suggest_chips 末尾而不是 _llm_chips 里，就是为了覆盖这条路径 ——
        LLM 挂了的时候正是最不该给重复建议的时候。
        """
        history = [{"role": "user", "text": "帮我看看哪一段最危险？"}]
        out = suggest.suggest_chips("site", None, history, [], 3, {"has_result": True})
        prompts = [c["prompt"] for c in out["chips"]]
        self.assertNotIn("帮我看看哪一段最危险？", prompts)
        # 仍应给出别的建议，不是一条都不给
        self.assertTrue(out["chips"], "去重后不该把胶囊全清空")


class TestCountAndKeep(unittest.TestCase):
    """点胶囊只补一条、留着的那两条不许和新胶囊撞车。"""

    def test_count_is_respected(self):
        """count=1 时只给一条 —— 这就是「点了胶囊只补一条」的后端落点。"""
        history = [{"role": "user", "text": "哪一段最颠？"}]
        out = suggest.suggest_chips("feasibility", None, history, [], 1, {"has_result": True})
        self.assertLessEqual(len(out["chips"]), 1, f"count=1 却给了 {len(out['chips'])} 条")

    def test_count_is_clamped_to_max(self):
        """前端乱传大数不能撑爆输出（端点公网无鉴权）。"""
        history = []
        out = suggest.suggest_chips("feasibility", None, history, [], 99, {})
        self.assertLessEqual(len(out["chips"]), suggest.MAX_CHIPS)

    def test_new_chip_must_not_collide_with_kept_ones(self):
        """留着的那两条会进避让清单：新胶囊若和它们问同一件事，就是白占一个位置。"""
        keep = [{"label": "A", "prompt": "这条航线什么时刻起飞最安全？"}]
        # 让规则兜底产出的句式与 keep 里的那条完全相同，验证它被挡住
        history = [{"role": "user", "text": "这条航线什么时刻起飞最安全？"}]
        out = suggest.suggest_chips("time_window", None, history, keep, 1, {"has_result": True})
        self.assertNotIn("这条航线什么时刻起飞最安全？",
                         [c["prompt"] for c in out["chips"]])

    def test_empty_history_is_safe(self):
        out = suggest.suggest_chips("feasibility", None, [], [], 3, {})
        self.assertTrue(out["chips"], "没有历史时也该给出开场建议")


class TestFmtHistory(unittest.TestCase):
    def test_text_only_and_chronological(self):
        h = [
            {"role": "user", "text": "第一问"},
            {"role": "assistant", "text": "第一答"},
            {"role": "user", "text": "第二问"},
        ]
        s = suggest._fmt_history(h)
        self.assertLess(s.index("第一问"), s.index("第一答"))
        self.assertLess(s.index("第一答"), s.index("第二问"))

    def test_long_history_is_capped(self):
        h = [{"role": "user", "text": "问" * 5000} for _ in range(10)]
        self.assertLessEqual(len(suggest._fmt_history(h, limit=12, cap=3000)), 3200)

    def test_keeps_newest_when_over_budget(self):
        """装不下时丢**最早**的 —— 最近几轮才是判断「还剩什么角度」的依据。"""
        h = [{"role": "user", "text": "最早的一问"}, {"role": "user", "text": "最近的一问"}]
        s = suggest._fmt_history(h, limit=1)
        self.assertIn("最近的一问", s)
        self.assertNotIn("最早的一问", s)

    def test_empty_history_returns_placeholder(self):
        self.assertEqual(suggest._fmt_history([]), "（无）")
        self.assertEqual(suggest._fmt_history(None), "（无）")

    def test_blank_turns_are_skipped(self):
        s = suggest._fmt_history([{"role": "user", "text": "   "}, {"role": "user", "text": "实的"}])
        self.assertIn("实的", s)
        self.assertEqual(s.count("："), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
