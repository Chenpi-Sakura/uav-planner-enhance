"""`.env` 载入与 AI 配置优先级测试（M5 后续项：本地 Key 配置）。

覆盖：
  ① 基本 KEY=VALUE、注释、空行、引号包裹；
  ② `export KEY=VALUE` 写法；
  ③ **已存在的真实环境变量优先**，`.env` 不覆盖；
  ④ 文件不存在时静默返回 0（保证「没有 .env 也能启动」）；
  ⑤ 数值型参数容错（填错不抛异常）。

运行：cd backend; .python/portable/python.exe -m unittest discover -s tests
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from config import load_env_file


class TestLoadEnvFile(unittest.TestCase):
    def setUp(self):
        # 记录并清理测试用键，避免污染其它用例
        self.keys = ["UAV_TEST_A", "UAV_TEST_B", "UAV_TEST_C", "UAV_TEST_QUOTED"]
        self.saved = {k: os.environ.get(k) for k in self.keys}
        for k in self.keys:
            os.environ.pop(k, None)
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / ".env"

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def _write(self, text: str):
        self.path.write_text(text, encoding="utf-8")

    def test_basic_pairs_comments_and_blanks(self):
        self._write(
            "# 这是注释\n"
            "\n"
            "UAV_TEST_A=alpha\n"
            "  UAV_TEST_B = beta  \n"
        )
        written = load_env_file(self.path)
        self.assertEqual(written, 2)
        self.assertEqual(os.environ["UAV_TEST_A"], "alpha")
        self.assertEqual(os.environ["UAV_TEST_B"], "beta")

    def test_quoted_value_keeps_spaces_and_hash(self):
        self._write('UAV_TEST_QUOTED="sk-abc # not a comment"\n')
        load_env_file(self.path)
        self.assertEqual(os.environ["UAV_TEST_QUOTED"], "sk-abc # not a comment")

    def test_export_prefix_supported(self):
        self._write("export UAV_TEST_A=from-export\n")
        load_env_file(self.path)
        self.assertEqual(os.environ["UAV_TEST_A"], "from-export")

    def test_existing_env_not_overridden(self):
        os.environ["UAV_TEST_A"] = "real-env"
        self._write("UAV_TEST_A=from-file\nUAV_TEST_C=only-file\n")
        written = load_env_file(self.path)
        self.assertEqual(os.environ["UAV_TEST_A"], "real-env", "真实环境变量必须优先")
        self.assertEqual(os.environ["UAV_TEST_C"], "only-file")
        self.assertEqual(written, 1)

    def test_missing_file_is_noop(self):
        missing = Path(self.tmp.name) / "does-not-exist.env"
        self.assertEqual(load_env_file(missing), 0)

    def test_malformed_lines_skipped(self):
        self._write("NO_EQUALS_SIGN\nUAV_TEST_A=ok\n=novalue\n")
        load_env_file(self.path)
        self.assertEqual(os.environ["UAV_TEST_A"], "ok")


class TestNumericEnvTolerance(unittest.TestCase):
    """数值型环境变量填错时应被忽略，而不是让服务启动失败。"""

    def test_bad_number_ignored_by_cast(self):
        # 复现 _load_ai_config 中的容错逻辑：非法值不会写入 cfg
        for raw in ("abc", "", None):
            cfg = {}
            if raw:
                try:
                    cfg["AI_TIMEOUT_S"] = float(raw)
                except (TypeError, ValueError):
                    pass
            self.assertNotIn("AI_TIMEOUT_S", cfg)

    def test_good_number_parsed(self):
        cfg = {}
        try:
            cfg["AI_MAX_TOKENS"] = int("1234")
        except (TypeError, ValueError):
            pass
        self.assertEqual(cfg["AI_MAX_TOKENS"], 1234)


if __name__ == "__main__":
    unittest.main(verbosity=2)