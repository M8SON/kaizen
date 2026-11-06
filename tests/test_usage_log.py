import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core import usage_log

ROOT = Path(__file__).resolve().parent.parent


class UsageLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "usage.db"
        patcher = mock.patch.dict(os.environ, {"USAGE_LOG_ENABLED": "true", "USAGE_LOG_PATH": str(self.db)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def rows(self):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute("SELECT service, model, purpose, input_tokens, output_tokens,"
                                " cache_read_tokens, cache_write_tokens, credits, audio_ms FROM usage").fetchall()
        finally:
            conn.close()

    def test_records_claude_usage_object(self):
        usage = SimpleNamespace(input_tokens=120, output_tokens=30,
                                cache_read_input_tokens=5000, cache_creation_input_tokens=None)
        usage_log.record_claude("claude-haiku-5-5", usage, "turn")
        self.assertEqual(self.rows(), [("claude", "claude-haiku-5-5", "turn", 120, 30, 5000, 0, 0.0, 0)])

    def test_header_string_credits_and_audio(self):
        usage_log.record("elevenlabs", "eleven_flash_v2_5", credits="7")
        usage_log.record("meta_stt", "m", audio_ms=1440)
        self.assertEqual([r[7] for r in self.rows()], [7.0, 0.0])
        self.assertEqual([r[8] for r in self.rows()], [0, 1440])

    def test_disabled_writes_nothing(self):
        with mock.patch.dict(os.environ, {"USAGE_LOG_ENABLED": "false"}):
            usage_log.record("jev", "jev-1.14.0", input_tokens=300)
        self.assertFalse(self.db.exists())

    def test_unwritable_path_does_not_raise(self):
        with mock.patch.dict(os.environ, {"USAGE_LOG_PATH": "/proc/nope/usage.db"}):
            usage_log.record("jev", "jev-1.14.0", input_tokens=300)

    def test_cost_report_prices_each_service(self):
        usage_log.record("claude", "claude-sonnet-4-6", input_tokens=1_000_000, output_tokens=100_000,
                         cache_read_tokens=1_000_000, cache_write_tokens=0)   # 3 + 1.5 + 0.3
        usage_log.record("jev", "jev-1.14.0", input_tokens=1_000_000)         # 0.042
        usage_log.record("elevenlabs", "eleven_flash_v2_5", credits=3000)     # plan fee 6.00
        usage_log.record("meta_stt", "muse", audio_ms=600_000)                # 10 min = 0.03
        usage_log.record("claude", "claude-unknown", input_tokens=5)
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "cost_report.py")],
                             capture_output=True, text=True, check=True, env=os.environ.copy()).stdout
        self.assertIn("$    4.8000", out)
        self.assertIn("$   10.8720", out)  # total incl. plan fee
        self.assertIn("3,000 of 30,000 credits used (10%)", out)
        self.assertIn("Not priced", out)
        self.assertIn("claude claude-unknown", out)


class ElevenLabsCreditsTests(unittest.TestCase):
    def test_synth_records_character_cost_header(self):
        from core import voice_backends
        from tests.tts_doubles import elevenlabs_client
        client, _ = elevenlabs_client([b"\x00\x00"], credits="4")
        backend = voice_backends.ElevenLabsTTSBackend(voice_id="v", api_key="k", client=client)
        with mock.patch.object(usage_log, "record") as rec:
            list(backend._synth_audio("hi"))
        rec.assert_called_once_with("elevenlabs", "eleven_flash_v2_5", "speak", credits="4")


if __name__ == "__main__":
    unittest.main()
