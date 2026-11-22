"""診断本文の秘匿規則の契約を確かめる。"""

from __future__ import annotations

import io
import json

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


def test_diagnostic_tail_discards_partial_line() -> None:
    """窓が途中から始まる先頭行は、認証値の途中か判定できないため転載しない。"""
    data = b"x" * 1000 + b"https://" + b"A" * 150 + b"@example.org\nOK\n"
    with io.BytesIO(data) as stream:
        assert diagnostic_redaction.read_diagnostic_tail(stream, limit=64) == ("OK\n", True, len(data))


class _ChunkedStream(io.BytesIO):
    """1回の読取で最大`chunk`バイトだけ返し、見出しが読取の境界をまたぐ場合を作る。"""

    def __init__(self, data: bytes, chunk: int) -> None:
        super().__init__(data)
        self.chunk = chunk

    def read(self, size: int | None = -1, /) -> bytes:
        return super().read(self.chunk if size is None or size < 0 else min(size, self.chunk))


def _unterminated_key(lines: int) -> bytes:
    return f"-----BEGIN RSA {_KEY}-----\n".encode() + f"{_SECRET}\n".encode() * lines


@pytest.mark.parametrize("chunk", [1, 7, 65536])
def test_diagnostic_tail_keeps_key_state_from_before_window(chunk: int) -> None:
    """末尾窓より前で始まった終端の無い秘密鍵は、窓に開始行が無くても本文を転載しない。"""
    stream = _ChunkedStream(_unterminated_key(40), chunk)
    result = diagnostic_redaction.read_diagnostic_tail(stream, limit=64)
    assert _SECRET not in result[0]
    assert result[1]


@pytest.mark.parametrize("chunk", [1, 7, 65536])
def test_diagnostic_tail_resumes_after_key_end_outside_window(chunk: int) -> None:
    """窓より前で始まった鍵の終端より後ろの通常の行は転載する。"""
    data = _unterminated_key(40) + f"-----END RSA {_KEY}-----\n通常の行\n".encode()
    stream = _ChunkedStream(data, chunk)
    result = diagnostic_redaction.read_diagnostic_tail(stream, limit=64)
    assert _SECRET not in result[0]
    assert result[0].endswith("通常の行\n")


def test_formatted_json_value_on_next_line_is_redacted() -> None:
    """区切りの後で改行した整形済みJSONの資格情報も伏せ、JSONとして読める形を保つ。"""
    text = json.dumps({"password": _SECRET}, indent=2).replace('": "', '":\n    "')
    redacted = diagnostic_redaction.redact_secrets(text)
    assert _SECRET not in redacted
    assert json.loads(redacted) == {"password": diagnostic_redaction.REDACTED}


@pytest.mark.parametrize("separator", ["=", ":"])
def test_empty_assignment_keeps_next_log_line(separator: str) -> None:
    """値の無い代入の次の行は資格情報として扱わず、診断として残す。"""
    line = "ERROR: 最初の検査が失敗した"
    assert diagnostic_redaction.redact_secrets(f"EXAMPLE_TOKEN{separator}\n{line}") == f"EXAMPLE_TOKEN{separator}\n{line}"
