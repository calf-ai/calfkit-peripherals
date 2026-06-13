"""Behavioral tests for secret redaction (agent/redact.py).

Covers the real value-masking paths plus adversarial probes for over-redaction
(false positives that corrupt legitimate output) and false-negatives (secrets
that leak). Bugs are PINNED to current behavior with `# BUG:` notes — fixes are
a separate task.

``_REDACT_ENABLED`` is snapshotted at import (HERMES_REDACT_SECRETS, default on),
so redaction is enabled in-process; tests pass ``force=True`` to be independent of
the env, except the two that exercise the enable/disable gate directly.
"""
import pytest

from calfkit_tools.hermes._vendor.agent import redact
from calfkit_tools.hermes._vendor.agent.redact import (
    mask_secret,
    redact_sensitive_text,
    _mask_token,
    _redact_form_body,
)


# ---------------------------------------------------------------------------
# Enable / disable / force gate (lines 348-355)
# ---------------------------------------------------------------------------

def test_none_int_and_empty_inputs(monkeypatch):
    monkeypatch.setattr(redact, "_REDACT_ENABLED", True)
    assert redact_sensitive_text(None) is None
    assert redact_sensitive_text(12345) == "12345"  # coerced to str, no secret
    assert redact_sensitive_text("") == ""


def test_disabled_passes_through_unless_forced(monkeypatch):
    monkeypatch.setattr(redact, "_REDACT_ENABLED", False)
    secret = "ghp_abcdefghij1234567890"
    assert redact_sensitive_text(secret) == secret           # disabled → raw
    assert redact_sensitive_text(secret, force=True) != secret  # force overrides


def test_enabled_by_default_redacts_without_force(monkeypatch):
    monkeypatch.setattr(redact, "_REDACT_ENABLED", True)
    out = redact_sensitive_text("ghp_abcdefghij1234567890")
    assert "ghp_abcdefghij1234567890" not in out
    assert "ghp_ab" in out and "7890" in out  # partial mask (6 head / 4 tail)


# ---------------------------------------------------------------------------
# Known-prefix tokens, ENV assignments, JSON, auth headers, JWT, private key
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "secret, head",
    [
        ("ghp_abcdefghij1234567890", "ghp_ab"),
        ("sk-abcdefghij1234567890XY", "sk-abc"),
        ("AIza" + "b" * 35, "AIzab"),
        ("xai-" + "c" * 33, "xai-cc"),
    ],
)
def test_prefix_tokens_partially_masked(secret, head):
    out = redact_sensitive_text(f"token is {secret} ok", force=True)
    assert secret not in out
    assert head in out  # 6-char head preserved for debuggability


def test_env_assignment_value_masked():
    out = redact_sensitive_text("OPENAI_API_KEY=sk-abcdefghij1234567890", force=True)
    assert "sk-abcdefghij1234567890" not in out
    assert out.startswith("OPENAI_API_KEY=")


def test_json_field_value_masked():
    out = redact_sensitive_text('{"apiKey": "supersecretvalue1234567890"}', force=True)
    assert "supersecretvalue1234567890" not in out
    assert '"apiKey":' in out


def test_authorization_bearer_masked():
    out = redact_sensitive_text("Authorization: Bearer abcdefghij1234567890XYZ", force=True)
    assert "abcdefghij1234567890XYZ" not in out
    assert "Authorization: Bearer " in out


def test_jwt_masked():
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcDEFghiJKL"
    out = redact_sensitive_text(f"jwt={jwt}", force=True)
    assert jwt not in out


def test_private_key_block_replaced():
    block = "-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAK\n-----END RSA PRIVATE KEY-----"
    out = redact_sensitive_text(block, force=True)
    assert "[REDACTED PRIVATE KEY]" in out
    assert "MIIBOgIBAAJBAK" not in out


def test_db_connstring_password_masked_but_passwordless_untouched():
    assert redact_sensitive_text("postgres://user:hunter2pw@db:5432/x", force=True) == \
        "postgres://user:***@db:5432/x"
    # No `:password@` → nothing to mask.
    assert redact_sensitive_text("postgres://user@db:5432/x", force=True) == \
        "postgres://user@db:5432/x"


def test_e164_phone_short_and_long_branches():
    assert redact_sensitive_text("call +1234567 now", force=True) == "call +1****67 now"
    assert redact_sensitive_text("call +14155550123 now", force=True) == "call +141****0123 now"


def test_telegram_real_token_masked():
    out = redact_sensitive_text("bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw", force=True)
    assert out == "bot123456789:***"


# ---------------------------------------------------------------------------
# code_file mode: skips ENV/JSON name-based redaction, keeps prefix/JWT
# ---------------------------------------------------------------------------

def test_code_file_skips_env_pattern_but_prefix_still_masks():
    out = redact_sensitive_text("MAX_TOKEN_VALUE=sk-abcdefghij1234567890", code_file=True, force=True)
    # ENV name-based collapse is skipped (the assignment is NOT reduced to ***)...
    assert out != "MAX_TOKEN_VALUE=***"
    # ...but the known sk- prefix is still partially masked.
    assert "sk-abcdefghij1234567890" not in out
    assert "sk-abc" in out


# ---------------------------------------------------------------------------
# Form-urlencoded body: newline no-op vs clean sensitive k=v
# ---------------------------------------------------------------------------

def test_form_body_newline_is_noop():
    text = "password=plainval\nuser=jane"
    assert _redact_form_body(text) == text


def test_form_body_redacts_sensitive_key():
    out = redact_sensitive_text("password=secretvalue123&user=jane", force=True)
    assert out == "password=***&user=jane"


# ---------------------------------------------------------------------------
# mask_secret / _mask_token boundaries
# ---------------------------------------------------------------------------

def test_mask_secret_boundaries():
    assert mask_secret("sk-proj-abcdef1234567890") == "sk-p...7890"
    assert mask_secret("short") == "***"          # below floor → placeholder
    assert mask_secret("") == ""                   # falsy → empty default
    assert mask_secret("", empty="(not set)") == "(not set)"


def test_mask_token_empty_returns_placeholder():
    assert _mask_token("") == "***"               # historical "***" not ""
    assert _mask_token("tiny") == "***"           # below 18 floor


# ---------------------------------------------------------------------------
# BUGS surfaced (pinned to CURRENT behavior — see coverage-improvement-plan.md §4)
# ---------------------------------------------------------------------------

def test_BUG_over_redacts_non_secret_key_containing_AUTH():
    # BUG: `_SECRET_ENV_NAMES` includes the bare substring `AUTH`, so any KEY
    # *containing* it (AUTHOR, COAUTHOR, AUTHENTICATED_USER) has its value masked
    # even though it is not a secret. Pins current (over-redacting) behavior.
    assert redact_sensitive_text("AUTHOR=jane", force=True) == "AUTHOR=***"
    assert redact_sensitive_text("COAUTHOR_NAME=bob", force=True) == "COAUTHOR_NAME=***"


def test_BUG_aws_secret_access_key_value_leaks_without_assignment():
    # BUG: only the AWS *access key ID* (AKIA…) is matched. The 40-char *secret
    # access key* has no pattern, so when it is not wrapped in a `…SECRET…=`
    # assignment it passes through UNREDACTED. Pins the false-negative (leak).
    secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    out = redact_sensitive_text(f"aws secret access key {secret}", force=True)
    assert secret in out  # leaks
    # Contrast: in a `…SECRET…=` assignment it IS masked (the ENV path catches it).
    masked = redact_sensitive_text(f"AWS_SECRET_ACCESS_KEY={secret}", force=True)
    assert secret not in masked


def test_BUG_telegram_regex_over_redacts_timestamp_like_ids():
    # BUG: `_TELEGRAM_RE` = (\d{8,}):([-A-Za-z0-9_]{30,}) matches any long-digit :
    # long-id pair — e.g. a nanosecond timestamp + request id — and masks it.
    text = "event 1700000000123:abcdefghijklmnopqrstuvwxyz0123456789 logged"
    out = redact_sensitive_text(text, force=True)
    assert out == "event 1700000000123:*** logged"
