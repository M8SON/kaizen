import logging
import time

from core import profiling


def test_disabled_by_default(monkeypatch, caplog):
    monkeypatch.delenv("MINICLAW_PROFILE", raising=False)
    profiling._refresh_enabled()
    with caplog.at_level(logging.INFO, logger="core.profiling"):
        with profiling.turn():
            with profiling.stage("foo"):
                pass
    assert "[TIMING-SUMMARY]" not in caplog.text


def test_records_stage_when_enabled(monkeypatch, caplog):
    monkeypatch.setenv("MINICLAW_PROFILE", "true")
    profiling._refresh_enabled()
    with caplog.at_level(logging.INFO, logger="core.profiling"):
        with profiling.turn():
            with profiling.stage("foo"):
                time.sleep(0.01)
    assert "[TIMING-SUMMARY]" in caplog.text
    assert "foo=" in caplog.text


def test_repeat_stage_is_suffixed(monkeypatch, caplog):
    monkeypatch.setenv("MINICLAW_PROFILE", "true")
    profiling._refresh_enabled()
    with caplog.at_level(logging.INFO, logger="core.profiling"):
        with profiling.turn():
            with profiling.stage("call"):
                pass
            with profiling.stage("call"):
                pass
            with profiling.stage("call"):
                pass
    assert "call=" in caplog.text
    assert "call_2=" in caplog.text
    assert "call_3=" in caplog.text


def test_stage_outside_turn_is_silent(monkeypatch, caplog):
    monkeypatch.setenv("MINICLAW_PROFILE", "true")
    profiling._refresh_enabled()
    with caplog.at_level(logging.INFO, logger="core.profiling"):
        with profiling.stage("orphan"):
            pass
    assert "[TIMING-SUMMARY]" not in caplog.text


def test_empty_turn_emits_no_line(monkeypatch, caplog):
    monkeypatch.setenv("MINICLAW_PROFILE", "true")
    profiling._refresh_enabled()
    with caplog.at_level(logging.INFO, logger="core.profiling"):
        with profiling.turn():
            pass
    assert "[TIMING-SUMMARY]" not in caplog.text
