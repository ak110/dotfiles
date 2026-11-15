"""agents_serverの共通診断ログ設定を検証する。"""

import asyncio
import json
import logging
import pathlib

import pytest

from agent_toolkit._agents_server import logging_config
from agent_toolkit._common import state_paths


def test_configure_logging_reuses_handlers_and_rotation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """設定を繰り返しても同じ保存先へログを一度だけ記録する。

    loggerのhandlerの退避と回収は`pytest_plugin`の自動fixtureが行い、試験は空のhandler一覧から始まる。
    """
    logger = logging.getLogger("agent-toolkit.agents-server")
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    first = logging_config.configure_logging()
    second = logging_config.configure_logging()
    assert first == second == tmp_path / "agents-server.log"
    message = "繰り返し設定後の識別可能な記録"
    logger.warning(message)
    logging_config.flush_logging()
    assert first.read_text(encoding="utf-8").count(message) == 1
    assert capsys.readouterr().err.count(message) == 1


def test_instance_diagnostics_independent_of_log_rotation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """共通ログの回転と別の起動単位の開始・終了は、記録済みの起動単位の終了診断を変えない。"""
    logger = logging.getLogger("agent-toolkit.agents-server")
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(logging_config, "_CURRENT_INSTANCE", None)
    monkeypatch.setattr(logging_config, "LOG_MAX_BYTES", 4096)
    try:
        first = logging_config.start_server_instance(environment={}, state_root=tmp_path)
        logging_config.record_instance_event("exited", outcome="normal", exception_type=None, message=None)
        recorded = first.path.read_text(encoding="utf-8")
        logging_config.configure_logging()
        for index in range(200):
            logger.warning("回転させるための記録 %d %s", index, "x" * 100)
        logging_config.flush_logging()
        second = logging_config.start_server_instance(environment={}, state_root=tmp_path)
        logging_config.record_instance_event("exited", outcome="exception", exception_type="RuntimeError", message="x")
    finally:
        # 回転後のファイルを確かめる前に、ファイル向けworkerの出力を完了させる。
        logging_config.close_logging()

    assert (tmp_path / "agents-server.log.3").exists()
    assert first.path.read_text(encoding="utf-8") == recorded
    assert second.instance_id != first.instance_id
    assert [json.loads(line)["event"] for line in recorded.splitlines()] == ["started", "exited"]
    reference = logging_config.diagnostics_reference(
        logging_config.InstanceReference(first.instance_id, 1), state_root=tmp_path
    )
    assert reference["status"] == "collected"


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_file_log_redacts_formatted_exceptions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, asynchronous: bool
) -> None:
    """未知の呼出元でも書式引数と例外チェーンを保存前に伏せる。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    path = logging_config.configure_logging()

    def emit() -> None:
        logging.getLogger("agent-toolkit.agents-server.mcp-tools").warning(
            "-----BEGIN %s-----\nfake-pem-value\n-----END %s-----\nsession-1 %s", "PRIVATE KEY", "PRIVATE KEY", "a" * 40
        )
        logger = logging.getLogger("agent-toolkit.agents-server.new-contract")
        try:
            try:
                raise ValueError("api_key=fake-chain-value")
            except ValueError as error:
                raise RuntimeError("https://fake-user:fake-pass@example.test/path E_TEST") from error
        except RuntimeError:
            logger.exception("Authorization: Bearer %s", "fake-bearer-value")

    if asynchronous:
        emit()
    else:
        await asyncio.to_thread(emit)
    logging_config.flush_logging()
    saved = path.read_text(encoding="utf-8")
    for secret in ("fake-chain-value", "fake-user", "fake-pass", "fake-bearer-value", "fake-pem-value"):
        assert secret not in saved
    assert "session-1" in saved and "a" * 40 in saved
    assert "RuntimeError" in saved and "ValueError" in saved and "example.test/path E_TEST" in saved


def test_instance_redacts_nested_values(tmp_path: pathlib.Path) -> None:
    """診断のキーと構造を保ち、入れ子の文字列値だけを伏せる。"""
    path = tmp_path / "instance.jsonl"
    assert logging_config.append_instance_event(
        path, writer="server", event="exited", detail={"password": ["password=fake-nested", 7]}
    )
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "fake-nested" not in str(saved)
    assert saved["detail"]["password"][1] == 7


def test_instance_redacts_credential_fields_and_split_text(tmp_path: pathlib.Path) -> None:
    """裸の構造化資格情報とJSON化・切断済みの診断を保存境界へ渡す。"""
    path = tmp_path / "instance.jsonl"
    detail = {
        "api_key": "fake-bare-key",
        "Proxy-Authorization": "fake-proxy-auth",
        "nested": [{"Authorization": "fake-bare-auth", "password": "fake-bare-password"}],
        "max_tokens": 123,
        "escaped": json.dumps(json.dumps({"token": "fake-escaped-token"})),
        "cut": "-----BEGIN " + "PRIVATE KEY-----\nfake-cut-key",
        "message": "Authorization: Bearer fake-header code=E401 host=example.test stage=start",
    }
    assert logging_config.append_instance_event(path, writer="server", event="exited", detail=detail)
    text = path.read_text(encoding="utf-8")
    for secret in (
        "fake-bare-key",
        "fake-proxy-auth",
        "fake-bare-auth",
        "fake-bare-password",
        "fake-escaped-token",
        "fake-cut-key",
        "fake-header",
    ):
        assert secret not in text
    saved = json.loads(text)["detail"]
    assert saved["max_tokens"] == 123
    assert "code=E401 host=example.test stage=start" in saved["message"]
