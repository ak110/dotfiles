"""atk agents notifyの公開CLI契約を検証する。"""

import json
import pathlib
import re

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import status_file
from agent_toolkit._atk import config

_NOTICE_TAG_PATTERN = re.compile(
    r"\A<agent-toolkit-auto-inserted"
    r'(?=[^>]*\sfrom="delegate:(?P<sender>[^"]+)")'
    r'(?=[^>]*\scomposed-by="caller")'
    r'(?=[^>]*\ssource="agent-toolkit/agents-notify")'
    r'(?=[^>]*\skind="agent-delivery")[^>]*>\n'
    r"(?P<body>.*)\n</agent-toolkit-auto-inserted>\Z",
    re.DOTALL,
)


def _notice_body(delivered: str) -> str:
    """通知本文の出所標識を検証し、囲まれた逐語の本文を返す。"""
    matched = _NOTICE_TAG_PATTERN.fullmatch(delivered)
    assert matched is not None, delivered
    return matched["body"]


def test_notice_body_accepts_reordered_attributes_and_outer_body() -> None:
    """必要属性の順序が変わっても最後の終了タグまで本文を保つ。"""
    body = '<agent-toolkit-auto-inserted source="inner" kind="notice">内側</agent-toolkit-auto-inserted>\r\n次の行'
    delivered = (
        '<agent-toolkit-auto-inserted kind="agent-delivery" source="agent-toolkit/agents-notify"'
        ' composed-by="caller" from="delegate:child">\n'
        f"{body}\n</agent-toolkit-auto-inserted>"
    )
    assert _notice_body(delivered) == body


@pytest.mark.parametrize(
    "opening,closing",
    [
        (
            '<other from="delegate:child" composed-by="caller" source="agent-toolkit/agents-notify" kind="agent-delivery">',
            "</other>",
        ),
        (
            '<agent-toolkit-auto-inserted from="delegate:child" source="agent-toolkit/agents-notify" kind="agent-delivery">',
            "</agent-toolkit-auto-inserted>",
        ),
        (
            '<agent-toolkit-auto-inserted from="main:child" composed-by="caller"'
            ' source="agent-toolkit/agents-notify" kind="agent-delivery">',
            "</agent-toolkit-auto-inserted>",
        ),
        (
            '<agent-toolkit-auto-inserted from="delegate:child" composed-by="caller" source="wrong" kind="agent-delivery">',
            "</agent-toolkit-auto-inserted>",
        ),
        (
            '<agent-toolkit-auto-inserted from="delegate:child" composed-by="caller"'
            ' source="agent-toolkit/agents-notify" kind="wrong">',
            "</agent-toolkit-auto-inserted>",
        ),
        (
            '<agent-toolkit-auto-inserted from="delegate:child" composed-by="caller"'
            ' source="agent-toolkit/agents-notify" kind="agent-delivery">',
            "</other>",
        ),
    ],
)
def test_notice_body_rejects_invalid_boundary(opening: str, closing: str) -> None:
    """要素名、必要属性と外側の終了タグの相違を拒否する。"""
    with pytest.raises(AssertionError):
        _notice_body(f"{opening}\n本文\n{closing}")


@pytest.fixture(name="notify_environment")
def _notify_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    """委譲先の識別子と状態保存先をテスト用に隔離する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "root-session")
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setenv("CODEX_THREAD_ID", "child-session")
    monkeypatch.setattr(config, "state_dir", lambda: tmp_path)
    return status_file.notices_directory("root-session", tmp_path)


def test_agents_notify_preserves_body_exactly(
    notify_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """空白と改行を含む本文を正規化せず保存する。"""
    body = "  先頭\r\n末尾\n"

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "notify", "--body", body])

    paths = list(notify_environment.glob("child-session.*.json"))
    assert len(paths) == 1
    raw_payload = paths[0].read_text(encoding="utf-8")
    payload = json.loads(raw_payload)
    assert payload["version"] == 1
    assert payload["session_id"] == "child-session"
    assert isinstance(payload["sent_at"], str)
    assert _notice_body(payload["body"]) == body
    assert raw_payload.endswith("\n")
    assert raw_payload.count("\n") == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert not captured.err


def test_agents_notify_reads_body_file(
    notify_environment: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """絶対パスのUTF-8ファイルから本文を原文保持で読み込む。"""
    body_path = tmp_path / "body.txt"
    body_path.write_bytes("本文\r\n".encode())

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "notify", "--body-file", str(body_path)])

    payload = json.loads(next(notify_environment.glob("child-session.*.json")).read_text(encoding="utf-8"))
    assert _notice_body(payload["body"]) == "本文\r\n"


def test_agents_notify_reads_shell_metacharacters_from_body_file(
    notify_environment: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """シェルメタ文字を含む本文をファイル経由で原文保持する。"""
    body = "error: can't expand '$VALUE' or `command`\nnext line\n"
    body_path = tmp_path / "body.txt"
    body_path.write_bytes(body.encode())

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "notify", "--body-file", str(body_path)])

    payload = json.loads(next(notify_environment.glob("child-session.*.json")).read_text(encoding="utf-8"))
    assert _notice_body(payload["body"]) == body


def test_agents_notify_rejects_missing_delegated_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """委譲先の識別子が無い実行主体は終了コード4で拒否する。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(config, "state_dir", lambda: tmp_path)

    with pytest.raises(SystemExit, match="4"):
        atk.main(["agents", "notify", "--body", "通知"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "解決できません" in captured.err
    assert not (tmp_path / "agents-server").exists()


def test_agents_notify_rejects_root_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ルートsession自身からの送信は終了コード4で拒否する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "root-session")
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "root-session")
    monkeypatch.setattr(config, "state_dir", lambda: tmp_path)

    with pytest.raises(SystemExit, match="4"):
        atk.main(["agents", "notify", "--body", "通知"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "解決できません" in captured.err
    assert not (tmp_path / "agents-server").exists()


def test_agents_notify_rejects_blank_body(
    notify_environment: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """空白だけの本文は終了コード5で拒否し、ファイルを作成しない。"""
    with pytest.raises(SystemExit, match="5"):
        atk.main(["agents", "notify", "--body", " \n\t"])

    captured = capsys.readouterr()
    assert not captured.out
    assert "空白だけ" in captured.err
    assert not notify_environment.exists()


def test_agents_notify_uses_unique_name(notify_environment: pathlib.Path) -> None:
    """同じsessionの複数通知を上書きせず保存する。"""
    for body in ("1件目", "2件目"):
        with pytest.raises(SystemExit, match="0"):
            atk.main(["agents", "notify", "--body", body])

    assert len(list(notify_environment.iterdir())) == 2


def test_agents_notify_rejects_relative_body_file(capsys: pytest.CaptureFixture[str]) -> None:
    """相対本文ファイルはargparseの終了コード2で拒否する。"""
    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents", "notify", "--body-file", "body.txt"])
    assert "絶対パス" in capsys.readouterr().err
