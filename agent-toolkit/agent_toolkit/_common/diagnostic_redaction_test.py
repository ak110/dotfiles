"""診断本文の秘匿規則の契約を確かめる。"""

from __future__ import annotations

import pytest

from agent_toolkit._common import diagnostic_redaction

_SECRET = "s3cr3t-Value_0123456789abcdefXYZ"
# 秘密鍵の検出hookがリテラルの見出し行を実鍵と区別できないため、見出しの語は実行時に組み立てる。
_KEY = "PRIVATE" + " KEY"


@pytest.mark.parametrize(
    "text",
    [
        f"Authorization: Bearer {_SECRET}",
        f'{{"Authorization": "Basic {_SECRET}"}}',
        f"proxy-authorization={_SECRET}",
        f"OPENAI_API_KEY={_SECRET}",
        f'{{"refresh_token": "{_SECRET}"}}',
        f"password: '{_SECRET}'",
        f"git fetch https://user:{_SECRET}@github.com/ak110/dotfiles.git",
        f"https://{_SECRET}@example.com/repo",
        f"GET https://api.example.com/v1/items?id=3&access_token={_SECRET}&page=2",
        f"-----BEGIN OPENSSH {_KEY}-----\n{_SECRET}\nAAAA\n-----END OPENSSH {_KEY}-----",
        f"-----BEGIN RSA {_KEY}-----\n{_SECRET}\n(truncated)",
    ],
)
def test_credentials_are_redacted(text: str) -> None:
    redacted = diagnostic_redaction.redact_secrets(text)
    assert _SECRET not in redacted
    assert diagnostic_redaction.REDACTED in redacted


def test_non_secret_diagnostics_are_kept() -> None:
    text = (
        "RuntimeError: HTTP 401 from https://api.example.com/v1/items?id=3&page=2\n"
        "session_id=01a1d257-83f8-7693-8dd3-10e1bea8ba9b commit=bfa3e831100f92020e4a7f9201194e569ac771ed\n"
        "path=/home/aki/dotfiles/agent-toolkit/agent_toolkit/_common/diagnostic_redaction.py stage=start"
    )
    assert diagnostic_redaction.redact_secrets(text) == text


def test_redaction_keeps_line_structure_and_surrounding_text() -> None:
    redacted = diagnostic_redaction.redact_secrets(f"stage=fetch\nAuthorization: Bearer {_SECRET}\nexit=128")
    assert redacted.splitlines()[0] == "stage=fetch"
    assert redacted.splitlines()[2] == "exit=128"


@pytest.mark.parametrize("credential", [f"Bearer {_SECRET}", f"Basic {_SECRET}", _SECRET])
def test_authorization_preserves_following_diagnostics(credential: str) -> None:
    """同じ行で認証値に続く原因調査情報を保持する。"""
    suffix = "code=E401 host=example.test stage=start"
    saved = diagnostic_redaction.redact_secrets(f"Authorization: {credential} {suffix}")
    assert _SECRET not in saved
    assert suffix in saved
