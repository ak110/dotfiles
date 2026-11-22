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


def test_diagnostic_tail_redacts_whole_before_taking_tail() -> None:
    """全体を伏せてから末尾を取るため、末尾の開始位置が認証値の途中でも断片を残さない。"""
    data = b"x" * 1000 + b"https://" + b"A" * 150 + b"@example.org\nOK\n"
    with io.BytesIO(data) as stream:
        result = diagnostic_redaction.read_diagnostic_tail(stream, limit=64)
    assert result == (diagnostic_redaction.redact_secrets(data.decode()).encode()[-64:].decode(), True, len(data))
    assert "A" * 10 not in result.text


def test_diagnostic_tail_omits_body_over_retained_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """保持の上限を超えた出力は、全体を伏せられないため本文を転載せず、その旨を示す。"""
    monkeypatch.setattr(diagnostic_redaction, "RETAINED_BYTES", 100)
    tail = diagnostic_redaction.DiagnosticTail(64)
    for _ in range(10):
        tail.feed(f"{_SECRET}\n")
    result = tail.excerpt()
    assert _SECRET not in result.text
    assert result.omitted
    assert "全文は元の出力先で確認してください" in result.text


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
    data = _unterminated_key(40)
    result = diagnostic_redaction.read_diagnostic_tail(_ChunkedStream(data, chunk), limit=64)
    assert _SECRET not in result[0]
    assert result[0] == diagnostic_redaction.redact_secrets(data.decode())


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


@pytest.mark.parametrize("limit", [16 * 1024, 64 * 1024])
def test_diagnostic_tail_matches_whole_redaction_across_window(limit: int) -> None:
    """資格情報名が末尾の範囲より前の行にあっても、全体を伏せた結果と同じく値を転載しない（運用の上限16 KiB・64 KiB）。"""
    data = ('{"password":' + "\n" * (limit * 3) + f'"{_SECRET}"}}\n通常の行\n').encode()
    assert _SECRET not in diagnostic_redaction.redact_secrets(data.decode())
    with io.BytesIO(data) as stream:
        result = diagnostic_redaction.read_diagnostic_tail(stream, limit=limit)
    assert _SECRET not in result[0]
    assert result[0].endswith("通常の行\n")


@pytest.mark.parametrize("chunk", [1, 4096, 65536])
def test_diagnostic_tail_hides_key_with_long_header(chunk: int) -> None:
    """全体の秘匿が認識する長い見出しの鍵も、読取の境界によらず本文を転載しない。"""
    header = f"-----BEGIN {'X' * 3000} {_KEY}-----\n".encode()
    data = header + f"{_SECRET}\n".encode() * 40
    assert _SECRET not in diagnostic_redaction.redact_secrets(data.decode())
    result = diagnostic_redaction.read_diagnostic_tail(_ChunkedStream(data, chunk), limit=64)
    assert _SECRET not in result[0]
