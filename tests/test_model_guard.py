"""Local tests for Anthropic model guardrails (no completion API calls)."""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from swingtrade.model_guard import resolve_model_policy_status, validate_configured_models
from swingtrade.settings import Settings


@contextmanager
def _temp_env(**env: str):
    old = {k: os.environ.get(k) for k in env}
    try:
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _settings(tmp: str, *, haiku: str, sonnet: str, opus: str) -> Settings:
    return Settings(
        swingtrade_config_dir=tmp,
        ANTHROPIC_MODEL_HAIKU=haiku,
        ANTHROPIC_MODEL_SONNET=sonnet,
        ANTHROPIC_MODEL_OPUS=opus,
    )


def test_approved_models_pass():
    with tempfile.TemporaryDirectory() as tmp:
        s = _settings(
            tmp,
            haiku="claude-haiku-4-5-20251001",
            sonnet="claude-sonnet-4-6",
            opus="claude-opus-4-7",
        )
        r = validate_configured_models(s)
        assert r.ok
        assert all(e.status == "approved" for e in r.entries)


def test_opus_4_7_not_blocked_by_opus_4_prefix():
    status, reason = resolve_model_policy_status(
        "claude-opus-4-7", "opus", {"approved_models": {"opus": ["claude-opus-4-7"]}, "blocked_patterns": ["claude-opus-4"]}
    )
    assert status == "approved"
    assert "approved" in reason


def test_blocked_opus_4_fails_default():
    with tempfile.TemporaryDirectory() as tmp:
        s = _settings(
            tmp,
            haiku="claude-haiku-4-5-20251001",
            sonnet="claude-sonnet-4-6",
            opus="claude-opus-4",
        )
        with _temp_env(
            SWINGTRADE_ALLOW_DEPRECATED_MODELS=None,
            SWINGTRADE_ALLOW_UNKNOWN_MODELS=None,
        ):
            try:
                validate_configured_models(s)
                assert False, "expected ValueError"
            except ValueError as e:
                assert "Blocked model not allowed" in str(e)


def test_blocked_opus_4_1_fails_default():
    with tempfile.TemporaryDirectory() as tmp:
        s = _settings(
            tmp,
            haiku="claude-haiku-4-5-20251001",
            sonnet="claude-sonnet-4-6",
            opus="claude-opus-4-1",
        )
        with _temp_env(
            SWINGTRADE_ALLOW_DEPRECATED_MODELS=None,
            SWINGTRADE_ALLOW_UNKNOWN_MODELS=None,
        ):
            try:
                validate_configured_models(s)
                assert False, "expected ValueError"
            except ValueError as e:
                assert "Blocked model not allowed" in str(e)


def test_unknown_model_fails_default():
    with tempfile.TemporaryDirectory() as tmp:
        s = _settings(
            tmp,
            haiku="claude-haiku-4-5-20251001",
            sonnet="claude-sonnet-4-6",
            opus="claude-opus-next",
        )
        with _temp_env(
            SWINGTRADE_ALLOW_DEPRECATED_MODELS=None,
            SWINGTRADE_ALLOW_UNKNOWN_MODELS=None,
        ):
            try:
                validate_configured_models(s)
                assert False, "expected ValueError"
            except ValueError as e:
                assert "Unknown/unapproved model not allowed" in str(e)


def test_allow_deprecated_override():
    with tempfile.TemporaryDirectory() as tmp:
        s = _settings(
            tmp,
            haiku="claude-haiku-4-5-20251001",
            sonnet="claude-sonnet-4-6",
            opus="claude-opus-4-1",
        )
        with _temp_env(SWINGTRADE_ALLOW_DEPRECATED_MODELS="1"):
            r = validate_configured_models(s)
            assert r.ok
            assert any("DEPRECATED" in w.upper() for w in r.warnings)


def test_allow_unknown_override():
    with tempfile.TemporaryDirectory() as tmp:
        s = _settings(
            tmp,
            haiku="claude-haiku-4-5-20251001",
            sonnet="claude-sonnet-4-6",
            opus="claude-opus-next",
        )
        with _temp_env(SWINGTRADE_ALLOW_UNKNOWN_MODELS="1"):
            r = validate_configured_models(s)
            assert r.ok
            assert any("UNKNOWN" in w.upper() for w in r.warnings)


def test_no_auto_substitution():
    with tempfile.TemporaryDirectory() as tmp:
        opus_model = "claude-opus-4-7"
        s = _settings(
            tmp,
            haiku="claude-haiku-4-5-20251001",
            sonnet="claude-sonnet-4-6",
            opus=opus_model,
        )
        r = validate_configured_models(s)
        found = next(e for e in r.entries if e.family == "opus")
        assert found.model_id == opus_model


if __name__ == "__main__":
    tests = [
        test_approved_models_pass,
        test_opus_4_7_not_blocked_by_opus_4_prefix,
        test_blocked_opus_4_fails_default,
        test_blocked_opus_4_1_fails_default,
        test_unknown_model_fails_default,
        test_allow_deprecated_override,
        test_allow_unknown_override,
        test_no_auto_substitution,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    sys.exit(1 if failed else 0)
