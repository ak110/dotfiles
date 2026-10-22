"""公開startから候補切替、起動済みsessionの追送、taskの記録配送へつながる契約を検証する。"""

import datetime
import json
import os
import pathlib
import re
import sys
from typing import Any

import pytest

from agent_toolkit._agents_server import (
    agents_wait,
    backends,
    manager,
    mcp_tools,
    record_paths,
    session_errors,
    shared_layout,
    state,
    status_file,
    unavailable_candidates,
)
from agent_toolkit._common import atomic_file, session_registry, state_paths
from agent_toolkit._testing import agents_server_support

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


class _WindowsAccessDenied(PermissionError):
    """Windowsの`os.replace`が返すWinError 5と同じ識別値を持つ置換拒否。"""

    winerror = 5


class ObservedBackend(agents_server_support.FakeBackend):
    """モデル出力のある起動を、外部engineの代わりに公開startへ返す。"""

    async def start(self, *args: Any, **kwargs: Any) -> state.SessionState:
        session = await super().start(*args, **kwargs)
        session.model_output_observed = True
        session.publish_registry = True
        return session


class UnavailableProbeBackend(agents_server_support.FakeBackend):
    """モデルとsessionの作成前に、確定した探索環境の拒否を返す。"""

    probe_calls = 0

    async def start(self, *args: Any, **kwargs: Any) -> state.SessionState:
        del args, kwargs
        self.probe_calls += 1
        raise backends.StartCandidateUnavailable("candidateDependencyUnavailable")


@pytest.mark.parametrize("mcp_diagnostic", ["MCP接続を回復中: agents_server", "MCP接続を回復できない: pyfltr"])
@pytest.mark.asyncio
async def test_public_start_keeps_connection_and_mcp_diagnostics(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, mcp_diagnostic: str
) -> None:
    """起動時の接続診断と、MCP回復中・未回復の診断を公開startへ一緒に届ける。"""

    class DiagnosticBackend(ObservedBackend):
        async def start(self, *args: Any, **kwargs: Any) -> state.SessionState:
            session = await super().start(*args, **kwargs)
            session.claude_connection_diagnostics = ("接続設定の診断",)
            session.claude_mcp_diagnostics = (mcp_diagnostic,)
            return session

    async with manager.AgentsServerManager(None) as current:
        agents_server_support.install_backend(current, "claude", DiagnosticBackend(current.sessions, "claude"))
        monkeypatch.setattr(mcp_tools, "_MANAGER", current)
        response = await mcp_tools.start(mode="delegate", cwd=str(tmp_path), prompt="処理", model_type="claude:test/medium")
        assert response["warnings"] == ["接続設定の診断", mcp_diagnostic]


@pytest.mark.parametrize("engine", ["claude", "codex"])
@pytest.mark.parametrize("mismatched_runtime", [False, True])
@pytest.mark.asyncio
async def test_public_start_classifies_dependency_import_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, engine: str, mismatched_runtime: bool
) -> None:
    """Claude依存の読込失敗を次候補へ接続し、実行系の不一致を診断し、他engineの例外境界を保つ。"""
    async with manager.AgentsServerManager(None) as current:

        class MissingDependency(agents_server_support.FakeBackend):
            calls = 0

            async def start(self, *_args: Any, **_kwargs: Any) -> state.SessionState:
                self.calls += 1
                raise ModuleNotFoundError("No module named 'claude_agent_sdk'", name="claude_agent_sdk")

        next_engine = "codex" if engine == "claude" else "claude"
        unavailable = MissingDependency(current.sessions, engine)
        selected = ObservedBackend(current.sessions, next_engine)
        agents_server_support.install_backend(current, engine, unavailable)
        agents_server_support.install_backend(current, next_engine, selected)
        monkeypatch.setattr(mcp_tools, "_MANAGER", current)
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REVIEW_MODEL", f"{engine}:test/medium,{next_engine}:test/medium")
        if mismatched_runtime:
            environment = tmp_path / "recreated-venv"
            environment.mkdir()
            (environment / "pyvenv.cfg").write_text("version_info = 99.1.0\n", encoding="utf-8")
            monkeypatch.setattr(sys, "prefix", str(environment))
        if engine == "codex":
            with pytest.raises(ModuleNotFoundError):
                await mcp_tools.start(mode="delegate", cwd=str(tmp_path), prompt="処理", model_type="review")
            assert not selected.start_calls and not current.sessions
        else:
            response = await mcp_tools.start(mode="delegate", cwd=str(tmp_path), prompt="処理", model_type="review")
            assert response["session_id"] == "codex-session"
            assert len(selected.start_calls) == 1
            assert "claude-session" not in current.sessions
            reason = response["excluded_candidates"][0]["reason"]
            assert "claude_agent_sdk" in reason and "ホストを再読み込み" in reason
            if mismatched_runtime:
                assert "仮想環境のPython=99.1.0" in reason and "一致しません" in reason
        assert unavailable.calls == 1


@pytest.mark.asyncio
async def test_start_advances_only_for_confirmed_prelaunch_candidate_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """公開startは型付きの事前判定で不成立と確定した候補だけを除いて次の候補を起動し、別のmanagerもその除外記録を使う。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    async with manager.AgentsServerManager(None) as first:
        probe = UnavailableProbeBackend(first.sessions, "codex")
        selected = ObservedBackend(first.sessions, "claude")
        agents_server_support.install_backend(first, "codex", probe)
        agents_server_support.install_backend(first, "claude", selected)
        monkeypatch.setattr(mcp_tools, "_MANAGER", first)
        candidates = "codex:gpt-6-sol/medium,claude:test/medium"
        response = await mcp_tools.start(mode="explore", cwd=str(tmp_path), prompt="調査", model_type=candidates)
        assert response["session_id"] == "claude-session"
        assert response["excluded_candidates"][0]["reason"] == "candidateDependencyUnavailable"
        assert probe.probe_calls == 1
        assert not probe.start_calls
        assert len(selected.start_calls) == 1
        assert unavailable_candidates.load_unavailable_candidates(
            candidates, "explore", now=datetime.datetime.now(datetime.UTC)
        )

        async with manager.AgentsServerManager(None) as second:
            second_probe = UnavailableProbeBackend(second.sessions, "codex")
            second_selected = ObservedBackend(second.sessions, "claude")
            agents_server_support.install_backend(second, "codex", second_probe)
            agents_server_support.install_backend(second, "claude", second_selected)
            monkeypatch.setattr(mcp_tools, "_MANAGER", second)
            repeated = await mcp_tools.start(mode="explore", cwd=str(tmp_path), prompt="再調査", model_type=candidates)
            assert repeated["session_id"] == "claude-session"
            assert not second_probe.start_calls
            assert second_probe.probe_calls == 0
            assert len(second_selected.start_calls) == 1


@pytest.mark.asyncio
async def test_engine_dependency_exhaustion_is_request_local(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同engineの候補が共通の依存で起動不能なら一度だけ試し、原因と次の操作を返す。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    async with manager.AgentsServerManager(None) as current:

        class DependencyUnavailable(ObservedBackend):
            calls = 0

            async def start(self, *_args: Any, **_kwargs: Any) -> state.SessionState:
                self.calls += 1
                raise backends.StartCandidateUnavailable("dependencyUnavailable", scope="launch_engine")

        probe = DependencyUnavailable(current.sessions, "codex")
        agents_server_support.install_backend(current, "codex", probe)
        monkeypatch.setattr(mcp_tools, "_MANAGER", current)
        candidates = "codex:gpt-6-sol/medium,codex:gpt-6-astra/high"
        with pytest.raises(session_errors.ActionableRuntimeError) as raised:
            await mcp_tools.start(mode="explore", cwd=str(tmp_path), prompt="調査", model_type=candidates)
        message = str(raised.value)
        assert "no available model candidates" in message
        assert message.count("dependencyUnavailable") == 2
        assert raised.value.next_action
        assert probe.calls == 1
        assert not current.sessions
        assert not unavailable_candidates.load_unavailable_candidates(
            candidates, "explore", now=datetime.datetime.now(datetime.UTC)
        )


@pytest.mark.asyncio
async def test_prelaunch_candidate_exhaustion(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """全候補が型付きの事前判定で不成立のとき、候補ごとの理由と次の操作を公開startの失敗で返す。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    async with manager.AgentsServerManager(None) as current:
        probes = {engine: UnavailableProbeBackend(current.sessions, engine) for engine in ("codex", "claude")}
        for engine, probe in probes.items():
            agents_server_support.install_backend(current, engine, probe)
        monkeypatch.setattr(mcp_tools, "_MANAGER", current)
        candidates = "codex:gpt-6-sol/medium,claude:test/medium"
        with pytest.raises(session_errors.ActionableRuntimeError) as raised:
            await mcp_tools.start(mode="explore", cwd=str(tmp_path), prompt="調査", model_type=candidates)
        message = agents_server_support._actionable_message(raised.value)  # pylint: disable=protected-access
        assert "no available model candidates" in message
        assert message.count("candidateDependencyUnavailable") == 2
        assert [probe.probe_calls for probe in probes.values()] == [1, 1]
        assert not current.sessions
        assert all(not probe.start_calls for probe in probes.values())
        recorded = unavailable_candidates.load_unavailable_candidates(
            candidates, "explore", now=datetime.datetime.now(datetime.UTC)
        )
        assert set(recorded.values()) == {"candidateDependencyUnavailable"}
        assert {candidate[0] for candidate in recorded} == {"codex", "claude"}


@pytest.mark.asyncio
async def test_started_session_survives_projection_failure(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """表示保存の継続失敗後も、公開応答のIDを同じsend_messageへ使える。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    writer = status_file.StatusFileWriter(
        {},
        status_file.StatusFileIdentity("root-session", "root.json", "parent-thread"),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    async with manager.AgentsServerManager(writer) as current:
        backend = ObservedBackend(current.sessions, "claude")
        agents_server_support.install_backend(current, "claude", backend)
        monkeypatch.setattr(mcp_tools, "_MANAGER", current)
        writer.activate()
        original_write = status_file.atomic_write

        def reject_projection(path: pathlib.Path, content: str, **kwargs: Any) -> None:
            if path == writer.path:
                error = _WindowsAccessDenied(13, "表示ファイルの置換を拒否", str(path))
                raise error
            original_write(path, content, **kwargs)

        monkeypatch.setattr(status_file, "atomic_write", reject_projection)
        try:
            started = await mcp_tools.start(mode="delegate", cwd=str(tmp_path), prompt="処理", model_type="claude:test/medium")
            assert started["session_id"] == "claude-session"
            assert started["persistence_warnings"]
            registered = session_registry.resolve(started["session_id"])
            assert registered.state == session_registry.Resolution.RUNNING
            assert registered.resume_info is not None
            assert registered.resume_info.cwd == str(tmp_path)
            record = json.loads(
                (session_registry.registry_directory() / f"{started['session_id']}.json").read_text(encoding="utf-8")
            )
            assert record["launcher_session_id"] == "parent-thread"
            sent = await mcp_tools.send_message(started["session_id"], "続ける")
            assert sent["delivery"] == "steered"
            assert backend.send_calls == 1
            assert len(backend.start_calls) == 1
        finally:
            monkeypatch.setattr(status_file, "atomic_write", original_write)
            await current.close()


@pytest.mark.parametrize("engine", ("claude", "codex", "agy"))
@pytest.mark.asyncio
async def test_registration_failure_keeps_started_session_and_recovers_on_flush(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, engine: str
) -> None:
    """開始後の登録保存だけが失敗してもIDを返し、既存flushで同じ登録を復旧する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    writer = status_file.StatusFileWriter(
        {},
        status_file.StatusFileIdentity("root-session", "root.json", "parent-thread"),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    async with manager.AgentsServerManager(writer) as current:
        backend = ObservedBackend(current.sessions, engine)
        agents_server_support.install_backend(current, engine, backend)
        monkeypatch.setattr(mcp_tools, "_MANAGER", current)
        writer.activate()
        original_publish = session_registry.publish

        def refuse(*_args: Any, **_kwargs: Any) -> None:
            raise PermissionError("登録簿の置換を拒否")

        monkeypatch.setattr(session_registry, "publish", refuse)
        try:
            started = await mcp_tools.start(
                mode="delegate", cwd=str(tmp_path), prompt="処理", model_type=f"{engine}:test/medium"
            )
            assert started["session_id"] == f"{engine}-session"
            assert any(warning["scope"] == "registration" for warning in started["persistence_warnings"])
            monkeypatch.setattr(session_registry, "publish", original_publish)
            writer.flush()
            resolution = session_registry.resolve(started["session_id"])
            assert resolution.state == session_registry.Resolution.RUNNING
            assert resolution.resume_info is not None and resolution.resume_info.cwd == str(tmp_path)
            assert len(backend.start_calls) == 1
        finally:
            monkeypatch.setattr(session_registry, "publish", original_publish)
            await current.close()


@pytest.mark.asyncio
async def test_prelaunch_save_failure_does_not_register_an_unstarted_backend(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """backendの開始前の不成立と、起動済みのsessionを区別する。"""
    async with manager.AgentsServerManager(None) as current:

        class RejectedBeforeStart(agents_server_support.FakeBackend):
            async def start(self, *_args: Any, **_kwargs: Any) -> state.SessionState:
                raise PermissionError("起動前の保存が不成立")

        backend = RejectedBeforeStart(current.sessions, "claude")
        agents_server_support.install_backend(current, "claude", backend)
        monkeypatch.setattr(mcp_tools, "_MANAGER", current)
        with pytest.raises(PermissionError):
            await mcp_tools.start(mode="delegate", cwd=str(tmp_path), prompt="処理", model_type="claude:test/medium")
        assert not current.sessions
        assert not backend.start_calls


class ReplaceRefusal:
    """指定先への置換だけを、Windowsの継続するアクセス拒否として共通保存の置換段階で失敗させる。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, destination: pathlib.Path) -> None:
        self.destination = destination
        self.active = True
        self.attempts: list[tuple[str, str]] = []
        self._original = os.replace
        monkeypatch.setattr(atomic_file.os, "replace", self._replace)

    def _replace(self, source: Any, target: Any) -> None:
        if not self.active or pathlib.Path(target) != self.destination:
            self._original(source, target)
            return
        self.attempts.append((str(source), str(target)))
        error = _WindowsAccessDenied(13, "アクセスが拒否されました", str(source), None, str(target))
        raise error


class TerminalBeforeResponseBackend(ObservedBackend):
    """起動応答を返す前にturnが終端したsessionを返す。"""

    async def start(self, *args: Any, **kwargs: Any) -> state.SessionState:
        session = await super().start(*args, **kwargs)
        session.status = "completed"
        session.agent_message = "応答前に終えた報告"
        session.turn_completed = True
        session.touch()
        return session


@pytest.mark.parametrize(
    ("file_name", "host_session_id"), (("root.json", "parent-thread"), ("delegate-host.json", "delegate-host"))
)
@pytest.mark.parametrize("terminal_before_response", (False, True))
@pytest.mark.asyncio
async def test_nested_and_early_terminal_start_preserves_registration(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    file_name: str,
    host_session_id: str,
    terminal_before_response: bool,
) -> None:
    """表示射影を一度も保存できない書込主体でも、起動結果・待機登録・終端本文の回収が同じsessionへ届く。

    root以外の書込主体（入れ子の委譲）と、応答前に終端したsessionの双方で、
    公開応答のID・cwd・委譲元と、CLI待機の登録と終端結果を表示ファイルから独立して扱えることを確かめる。
    """
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    writer = status_file.StatusFileWriter(
        {},
        status_file.StatusFileIdentity("root-session", file_name, host_session_id),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    async with manager.AgentsServerManager(writer) as current:
        backend_type = TerminalBeforeResponseBackend if terminal_before_response else ObservedBackend
        backend = backend_type(current.sessions, "claude")
        agents_server_support.install_backend(current, "claude", backend)
        monkeypatch.setattr(mcp_tools, "_MANAGER", current)
        writer.activate()
        writer.path.unlink(missing_ok=True)
        refusal = ReplaceRefusal(monkeypatch, writer.path)
        try:
            started = await mcp_tools.start(mode="delegate", cwd=str(tmp_path), prompt="処理", model_type="claude:test/medium")
            session_id = started["session_id"]
            assert started["status"] == ("completed" if terminal_before_response else "running")
            assert started["root_session_id"] == "root-session"
            assert {warning["scope"] for warning in started["persistence_warnings"]} == {"projection"}
            assert "同じ`session_id`" in started["next_action"]
            assert refusal.attempts and not writer.path.exists()

            record = json.loads((session_registry.registry_directory() / f"{session_id}.json").read_text(encoding="utf-8"))
            assert record["launcher_session_id"] == host_session_id
            resolution = session_registry.resolve(session_id)
            assert resolution.resume_info is not None and resolution.resume_info.cwd == str(tmp_path)
            assert (shared_layout.wait_targets_directory("root-session", file_name, tmp_path) / f"{session_id}.json").is_file()

            if not terminal_before_response:
                agents_server_support._complete(current.sessions[session_id], message="起動後に終えた報告")  # pylint: disable=protected-access
            with pytest.raises(status_file.ProjectionWriteError):
                writer.flush()
            assert not writer.path.exists()

            environment = {"CLAUDE_CODE_SESSION_ID": "root-session"}
            if file_name != "root.json":
                environment["AGENT_TOOLKIT_STATUS_HOST_SESSION"] = host_session_id
            capsys.readouterr()
            code = agents_wait.wait_for_result(environment=environment, state_root=tmp_path)
            rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
            assert code == 0
            assert [(row["session_id"], row["status"]) for row in rows] == [(session_id, "completed")]
            expected = "応答前に終えた報告" if terminal_before_response else "起動後に終えた報告"
            assert expected in json.dumps(rows, ensure_ascii=False)
            assert len(backend.start_calls) == 1
        finally:
            refusal.active = False
            await current.close()


@pytest.mark.asyncio
async def test_close_during_projection_failure_still_releases_writer(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """終了時の表示保存が失敗しても、終端結果を残したうえで書込主体の後始末まで進む。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    writer = status_file.StatusFileWriter(
        {},
        status_file.StatusFileIdentity("root-session", "root.json", "parent-thread"),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    async with manager.AgentsServerManager(writer) as current:
        agents_server_support.install_backend(current, "claude", ObservedBackend(current.sessions, "claude"))
        monkeypatch.setattr(mcp_tools, "_MANAGER", current)
        writer.activate()
        refusal = ReplaceRefusal(monkeypatch, writer.path)
        started = await mcp_tools.start(mode="delegate", cwd=str(tmp_path), prompt="処理", model_type="claude:test/medium")
        close_error: OSError | None = None
        try:
            await current.close()
        except OSError as error:
            close_error = error
        finally:
            refusal.active = False
        released = writer.schedule not in state._TOUCH_LISTENERS  # pylint: disable=protected-access
        if close_error is not None or not released:
            # 後続の試験へ購読を残さないよう、故障を解除して後始末を完了させてから判定する。
            await current.close()
        result = shared_layout.results_directory("root-session", tmp_path) / f"{started['session_id']}.json"
        assert json.loads(result.read_text(encoding="utf-8"))["status"] == "interrupted"
        assert close_error is None
        assert released


@pytest.mark.parametrize("engine", ("claude", "codex"))
@pytest.mark.parametrize("record_count", (0, 1, 2))
@pytest.mark.asyncio
async def test_task_start_delivers_launcher_record_without_manual_input(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, engine: str, record_count: int
) -> None:
    """公開taskが直接の委譲元情報と、記録の一意性を起動本文へ配送する。"""
    monkeypatch.setenv("CODEX_THREAD_ID", "direct-parent")
    async with manager.AgentsServerManager(None) as current:
        backend = ObservedBackend(current.sessions, "claude")
        agents_server_support.install_backend(current, "claude", backend)
        monkeypatch.setattr(mcp_tools, "_MANAGER", current)
        monkeypatch.delenv("CODEX_THREAD_ID")
        paths = tuple(tmp_path / f"record-{index}.jsonl" for index in range(record_count))
        for path in paths:
            path.write_text("{}\n", encoding="utf-8")
        found = record_paths.SessionRecord(engine, paths) if paths else None
        monkeypatch.setattr(record_paths, "find_session_record", lambda _session_id, **_kwargs: found)
        role = pathlib.Path(__file__).parents[2] / "share" / "add-wi.subagent.md"
        await mcp_tools.start(
            cwd=str(tmp_path),
            subagent_md_path=str(role),
            model_type="claude:test/medium",
            extra_params={"投入する要求": "request=/repo=awi=記録の取得", "引き継ぎ記録先": str(tmp_path / "handoff.md")},
        )
        match = re.search(
            r'<atk-auto source="agents-server" kind="launcher-record">\n(.*?)\n</atk-auto>',
            backend.prompts[0],
            re.DOTALL,
        )
        assert match is not None
        delivered = json.loads(match[1])
        assert delivered["launcher_session_id"] == "direct-parent"
        assert delivered["status"] == ("missing" if record_count == 0 else "resolved" if record_count == 1 else "ambiguous")
        if record_count == 1:
            assert delivered["record_engine"] == engine
            assert delivered["record_path"] == str(paths[0])
        else:
            assert "record_path" not in delivered
