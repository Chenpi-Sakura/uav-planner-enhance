"""图层键/底图键的跨端一致性：后端工具契约必须等于界面上那些控件的 id。

「AI 打开了一个图层，但左栏复选框没勾上、于是关不掉，再手点一次还叠两层」——
这个 bug 在任何一端单独看都是对的：后端把 `wind` 当合法图层键，前端复选框的 id 是
`flow`，两份定义各自成立，拼起来才错位。所以这里按**文本**把两份定义对齐断言，
而不是各测各的。界面控件改名时，这里要跟着改。
"""
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

import tools
import vision


def _text(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def _block(rel: str, opener: str) -> str:
    m = re.search(opener + r".*?\{(.*?)\n\};", _text(rel), re.S)
    assert m, f"{rel} 里找不到 {opener}，键表结构变了"
    return m.group(1)


class OverlayKeyTest(unittest.TestCase):
    def test_tool_keys_match_the_checkboxes(self):
        """toggle_overlay 能开的键 == 左栏那几个复选框的 id，一个不多一个不少。"""
        block = re.search(r"const overlayOptions = \[(.*?)\];",
                          _text("frontend/src/components/Sidebar.vue"), re.S).group(1)
        self.assertEqual(set(re.findall(r"id:\s*'([a-z]+)'", block)), set(tools.OVERLAY_KEYS),
                         "工具契约与复选框 id 漂移：错的那一边会在 store.overlays 里"
                         "留下复选框管不到的键")

    def test_every_key_has_a_label_and_a_legend(self):
        names = _block("frontend/src/agentTools.ts", r"const OVERLAY_NAMES[^{]*")
        self.assertEqual(set(re.findall(r"([a-z]+):\s*'", names)), set(tools.OVERLAY_KEYS))
        legend = _block("frontend/src/components/OverlayLegend.vue", r"const legendConfigs[^{]*")
        self.assertTrue(set(tools.OVERLAY_KEYS) <= set(re.findall(r"'([a-z]+)':\s*{", legend)),
                        "开了图层却没有图例：用户看到的颜色就没有读数口径")

    def test_data_loader_draws_every_key(self):
        """图层键最终拼进 /api/weather/overlay 的 layer 参数，出图分支必须都认。

        漏一个不会报错：get_overlay_image 末尾的 else 会把陌生键画成 u 分量 ——
        「湿度」图层画出来是一张风场图。
        """
        src = _text("backend/data_loader.py")
        self.assertIn("layer_type in ('tke', 'edr')", src)
        for key in ("flow", "temp", "humidity", "qvapor"):
            self.assertIn(f"layer_type == '{key}'", src)

    def test_legacy_and_chinese_names_are_folded(self):
        for legacy, want in (("wind", "flow"), ("rh", "humidity"), ("q", "qvapor")):
            self.assertEqual(
                tools.validate_args("toggle_overlay", {"layer": legacy, "on": True})["layer"],
                want)
        self.assertEqual(tools.validate_args(
            "toggle_overlay", {"layer": "湿度", "on": True})["layer"], "humidity")
        # 认不出的键整个丢掉：前端收到空 args 会报「图层名不认得」，
        # 而不是往 store.overlays 里塞一个没人能取消勾选的键
        self.assertEqual(tools.validate_args("toggle_overlay", {"layer": "ozone", "on": True}), {})

    def test_vision_uses_the_same_keys(self):
        """look_at_image 的白名单与工具契约同源；旧键名折算，而不是回退成 TKE 图。"""
        self.assertEqual(set(vision._LAYER_KEYS), set(tools.OVERLAY_KEYS))
        self.assertEqual(set(vision._LAYER_LEGACY), {"wind", "rh", "q"})
        for old, new in vision._LAYER_LEGACY.items():
            self.assertEqual(tools.OVERLAY_ALIASES[old], new)


class BaseMapKeyTest(unittest.TestCase):
    def test_keys_match_the_base_map_buttons(self):
        src = _text("frontend/src/components/LayerSelector.vue")
        self.assertEqual(set(re.findall(r"switchLayer\('([a-z]+)'\)", src)), set(tools.BASE_MAPS))
        names = _block("frontend/src/agentTools.ts", r"const BASE_MAP_NAMES[^{]*")
        self.assertEqual(set(re.findall(r"([a-z]+):\s*'", names)), set(tools.BASE_MAPS))

    def test_chinese_names_are_folded(self):
        for word, key in (("卫星图", "satellite"), ("地形", "terrain"), ("街道", "street")):
            self.assertEqual(tools.validate_args("set_base_map", {"map": word})["map"], key)
        self.assertEqual(tools.validate_args("set_base_map", {"map": "waterfall"}), {})


if __name__ == "__main__":
    unittest.main()
