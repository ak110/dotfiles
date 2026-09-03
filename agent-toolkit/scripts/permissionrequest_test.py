"""agent-toolkit/scripts/permissionrequest.py の無条件許可と記録のテスト。"""

import json
import pathlib

import _fork_runner
import permissionrequest as hook
import pytest

_SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "hook.py"

_ALLOW_RESPONSE = {
    "hookSpecificOutput": {
        "hookEventName": "PermissionRequest",
        "decision": {"behavior": "allow"},
    }
}


@pytest.fixture(autouse=True)
def _isolate_owner_session_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """最上位セッションの識別子の解決元となる環境変数を、テスト実行環境から切り離す。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)


@pytest.fixture(name="state_dir")
def _state_dir(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """記録先の状態ディレクトリをテスト用一時ディレクトリへ差し替える。"""
    state_dir = tmp_path / "state"
    monkeypatch.setattr(hook._atk_config, "state_dir", lambda: state_dir)  # noqa: SLF001  # pylint: disable=protected-access
    return state_dir


def _log_path(state_dir: pathlib.Path) -> pathlib.Path:
    """記録先ファイルのパスを返す。"""
    return state_dir / "permissionrequest.log"


def _rotated_log_path(state_dir: pathlib.Path) -> pathlib.Path:
    """退避先ファイルのパスを返す。"""
    return state_dir / "permissionrequest.log.1"


@pytest.mark.usefixtures("state_dir")
class TestAllowAlways:
    """入力によらず許可の応答を返す契約を検証する。"""

    @pytest.mark.parametrize(
        "payload_text",
        [
            json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}),
            json.dumps({"tool_name": "Write", "tool_input": {"file_path": "/etc/passwd", "content": "x"}}),
            json.dumps({"tool_name": "Read", "tool_input": {"file_path": "/etc/shadow"}}),
            json.dumps({"tool_name": "mcp__example__tool", "tool_input": {"arg": 1}}),
            json.dumps({"tool_name": "UnknownFutureTool", "tool_input": {}}),
            json.dumps({"tool_name": "Bash", "tool_input": "文字列のtool_input"}),
            json.dumps(["JSONだが辞書ではない"]),
            "{不正なJSON",
            "",
        ],
    )
    def test_returns_allow(self, payload_text: str, capsys: pytest.CaptureFixture[str]) -> None:
        assert hook.main(payload_text) == 0
        assert json.loads(capsys.readouterr().out) == _ALLOW_RESPONSE


class TestRecord:
    """許可した要求の記録内容と退避を検証する。"""

    def test_appends_one_json_line_per_request(self, state_dir: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """要求ごとに1行1レコードのJSONが追記され、payload由来の項目を保持する。"""
        payloads = [
            {"session_id": "s1", "cwd": "/home/user/repo", "tool_name": "Bash", "tool_input": {"command": "ls"}},
            {"session_id": "s2", "cwd": "/home/user/other", "tool_name": "Read", "tool_input": {"file_path": "/tmp/x"}},
        ]
        for payload in payloads:
            assert hook.main(json.dumps(payload)) == 0
        capsys.readouterr()

        lines = _log_path(state_dir).read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        for line, payload in zip(lines, payloads, strict=True):
            record = json.loads(line)
            assert record["session_id"] == payload["session_id"]
            assert record["cwd"] == payload["cwd"]
            assert record["tool_name"] == payload["tool_name"]
            assert record["tool_input"] == payload["tool_input"]
            assert record["root_session_id"] is None
            # UTCのISO 8601表記であることを、時差表記の有無で判定する。
            assert record["time"].endswith("+00:00")

    def test_missing_payload_keys_are_recorded_as_null(
        self, state_dir: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """payloadに無いキーはnullとして残り、後段の集計で欠落と区別できる。"""
        assert hook.main(json.dumps({"tool_name": "Bash"})) == 0
        capsys.readouterr()

        record = json.loads(_log_path(state_dir).read_text(encoding="utf-8"))
        assert record["tool_name"] == "Bash"
        assert record["session_id"] is None
        assert record["cwd"] is None
        assert record["tool_input"] is None
        assert record["root_session_id"] is None

    def test_unparsable_payload_is_recorded_with_null_fields(
        self, state_dir: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """解釈できないpayloadでも記録は残り、payload由来の項目はnullになる。"""
        assert hook.main("{不正なJSON") == 0
        capsys.readouterr()

        record = json.loads(_log_path(state_dir).read_text(encoding="utf-8"))
        assert record["tool_name"] is None
        assert record["time"].endswith("+00:00")

    @pytest.mark.parametrize(
        ("environment", "expected"),
        [
            ({"AGENT_TOOLKIT_OWNER_SESSION": "root-session", "CLAUDE_CODE_SESSION_ID": "child-session"}, "root-session"),
            ({"CLAUDE_CODE_SESSION_ID": "child-session"}, "child-session"),
            ({}, None),
        ],
    )
    def test_records_root_session_id_resolved_from_environment(
        self,
        environment: dict[str, str],
        expected: str | None,
        state_dir: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """委譲の起点となった最上位セッションの識別子を、委譲元が渡す変数を優先して記録する。"""
        for key, value in environment.items():
            monkeypatch.setenv(key, value)

        assert hook.main(json.dumps({"session_id": "child-session", "tool_name": "Bash"})) == 0
        capsys.readouterr()

        record = json.loads(_log_path(state_dir).read_text(encoding="utf-8"))
        assert record["root_session_id"] == expected
        assert record["session_id"] == "child-session"

    def test_rotates_when_log_reaches_size_limit(self, state_dir: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """上限へ達した記録先は追記前に退避され、新しい記録先へ書き直される。"""
        state_dir.mkdir(parents=True)
        old_content = "x" * hook._LOG_SIZE_LIMIT  # noqa: SLF001  # pylint: disable=protected-access
        _log_path(state_dir).write_text(old_content, encoding="utf-8")

        assert hook.main(json.dumps({"tool_name": "Bash"})) == 0
        capsys.readouterr()

        assert _rotated_log_path(state_dir).read_text(encoding="utf-8") == old_content
        assert json.loads(_log_path(state_dir).read_text(encoding="utf-8"))["tool_name"] == "Bash"

    def test_rotation_overwrites_existing_rotated_log(
        self, state_dir: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """退避は1世代だけ保持し、既存の退避先を上書きする。"""
        state_dir.mkdir(parents=True)
        old_content = "y" * hook._LOG_SIZE_LIMIT  # noqa: SLF001  # pylint: disable=protected-access
        _log_path(state_dir).write_text(old_content, encoding="utf-8")
        _rotated_log_path(state_dir).write_text("さらに古い世代", encoding="utf-8")

        assert hook.main(json.dumps({"tool_name": "Bash"})) == 0
        capsys.readouterr()

        assert _rotated_log_path(state_dir).read_text(encoding="utf-8") == old_content

    def test_keeps_log_when_below_size_limit(self, state_dir: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """上限未満の記録先は退避せず追記を続ける。"""
        state_dir.mkdir(parents=True)
        _log_path(state_dir).write_text("z" * (hook._LOG_SIZE_LIMIT - 1), encoding="utf-8")  # noqa: SLF001  # pylint: disable=protected-access

        assert hook.main(json.dumps({"tool_name": "Bash"})) == 0
        capsys.readouterr()

        assert not _rotated_log_path(state_dir).exists()

    def test_returns_allow_when_log_is_unwritable(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """記録先を作成できない場合も許可の応答を返す。"""
        blocked = tmp_path / "blocked"
        blocked.write_text("状態ディレクトリの位置にある通常ファイル", encoding="utf-8")
        monkeypatch.setattr(hook._atk_config, "state_dir", lambda: blocked)  # noqa: SLF001  # pylint: disable=protected-access

        assert hook.main(json.dumps({"tool_name": "Bash"})) == 0
        assert json.loads(capsys.readouterr().out) == _ALLOW_RESPONSE


class TestEndToEnd:
    """サブプロセス経由で stdin / stdout の応答を検証する。"""

    def _run(self, payload_text: str, state_dir: pathlib.Path) -> tuple[int, str]:
        result = _fork_runner.run_script(
            _SCRIPT_PATH,
            argv=("permissionrequest",),
            input=payload_text,
            env={"XDG_STATE_HOME": str(state_dir)},
            timeout=30,
        )
        return result.returncode, result.stdout

    def test_bash_request_returns_allow(self, tmp_path: pathlib.Path) -> None:
        payload_text = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
        code, stdout = self._run(payload_text, tmp_path)
        assert code == 0
        assert json.loads(stdout) == _ALLOW_RESPONSE

    def test_invalid_json_input_returns_allow(self, tmp_path: pathlib.Path) -> None:
        code, stdout = self._run("not-json", tmp_path)
        assert code == 0
        assert json.loads(stdout) == _ALLOW_RESPONSE
