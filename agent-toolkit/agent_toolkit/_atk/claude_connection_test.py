"""役割別接続の選択と、認証環境を持たない候補の除外を検証する。"""

import argparse
import json
import pathlib
import subprocess
from typing import Any, cast

import pytest

from agent_toolkit._atk import claude_connection as connection
from agent_toolkit._atk import orchestrator
from agent_toolkit._atk.wi import process_loop_env, process_loop_session
from agent_toolkit._common import claude_connection as settings
from agent_toolkit._common import state_paths


def _write_env(path: pathlib.Path) -> str:
    path.write_text("ANTHROPIC_API_KEY=test-key\n", encoding="utf-8")
    return str(path)


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    credits_file: str = "",
    payg: str = "",
    orchestrator_order: str | None = None,
    worker_order: str | None = None,
    subscription: bool = True,
) -> None:
    """Claudeの接続設定とサブスクリプションの認証を環境変数で与える。"""
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_CREDITS_ENV_FILE", credits_file)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_PAYG_ENV_FILE", payg)
    for name, value in (
        ("AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_CONNECTIONS", orchestrator_order),
        ("AGENT_TOOLKIT_CONFIG_CLAUDE_WORKER_CONNECTIONS", worker_order),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "subscription" if subscription else "")


@pytest.mark.parametrize("role", ["orchestrator", "delegate"])
@pytest.mark.parametrize(
    ("available", "orchestrator_expected", "delegate_expected"),
    [
        # 使える種別が1つ・2つ・3つの環境で、同じ使用順のまま未設定の種別だけが除かれる。
        ({"subscription"}, ["subscription"], ["subscription"]),
        ({"credits"}, ["credits"], ["credits"]),
        ({"payg"}, ["payg"], ["payg"]),
        ({"subscription", "credits"}, ["subscription", "credits"], ["credits", "subscription"]),
        ({"subscription", "payg"}, ["subscription", "payg"], ["subscription", "payg"]),
        ({"credits", "payg"}, ["credits", "payg"], ["credits", "payg"]),
        ({"subscription", "credits", "payg"}, ["subscription", "credits", "payg"], ["credits", "subscription", "payg"]),
    ],
)
def test_candidates_skip_unconfigured_kinds(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    role: str,
    available: set[str],
    orchestrator_expected: list[str],
    delegate_expected: list[str],
) -> None:
    """3種別を並べた使用順から、認証を設定していない種別を診断なしで除く。"""
    _configure(
        monkeypatch,
        credits_file=_write_env(tmp_path / "credits.env") if "credits" in available else "",
        payg=_write_env(tmp_path / "payg.env") if "payg" in available else "",
        orchestrator_order="subscription,credits,payg",
        worker_order="credits,subscription,payg",
        subscription="subscription" in available,
    )
    choices = connection.candidates(role)
    expected = orchestrator_expected if role == "orchestrator" else delegate_expected
    assert [item.kind for item in choices.connections] == expected
    assert not choices.warnings
    paths = {item.kind: item.api_env_file for item in choices.connections}
    assert paths.get("credits") in {None, str(tmp_path / "credits.env")}
    assert paths.get("payg") in {None, str(tmp_path / "payg.env")}


@pytest.mark.parametrize("role", ["orchestrator", "delegate"])
def test_payg_is_used_only_when_listed(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    """従量課金のenvファイルを設定しても、初期値の使用順（paygを含まない）では候補にしない。"""
    _configure(monkeypatch, payg=_write_env(tmp_path / "payg.env"), subscription=False)
    choices = connection.candidates(role)
    assert not choices.connections
    with pytest.raises(ValueError, match="Claude接続候補がありません"):
        choices.require_first()
    key = "claude_orchestrator_connections" if role == "orchestrator" else "claude_worker_connections"
    with pytest.raises(ValueError, match=key):
        choices.require_first()


def test_cli_orchestrator_excludes_credits(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`claude` CLIで起動する統括担当はクレジットを除いて診断を出し、SDKの会話はクレジットを使う。"""
    _configure(monkeypatch, credits_file=_write_env(tmp_path / "credits.env"), orchestrator_order="credits,subscription")
    cli = connection.candidates("orchestrator", cli=True)
    assert [item.kind for item in cli.connections] == ["subscription"]
    assert connection.CLI_CREDITS_EXCLUDED in cli.warnings
    assert [item.kind for item in connection.candidates("orchestrator").connections] == ["credits", "subscription"]


def test_unmanaged_environment_inherits(monkeypatch: pytest.MonkeyPatch) -> None:
    """認証キーと使用順のどれも設定していない環境は、親環境を引き継ぐ接続だけを返す。"""
    for key in (
        "CLAUDE_CREDITS_ENV_FILE",
        "CLAUDE_PAYG_ENV_FILE",
        "CLAUDE_ORCHESTRATOR_CONNECTIONS",
        "CLAUDE_WORKER_CONNECTIONS",
    ):
        monkeypatch.delenv(f"AGENT_TOOLKIT_CONFIG_{key}", raising=False)
    assert [item.kind for item in connection.candidates("delegate").connections] == ["inherit"]
    assert [item.kind for item in connection.candidates("orchestrator", cli=True).connections] == ["inherit"]


@pytest.mark.parametrize("invalid", ["missing", "directory", "malformed", "unreadable", "empty-file", "comments-only"])
def test_invalid_env_file_disables_api_candidate(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid: str,
) -> None:
    """無効パスは保存値を保ち、起動時の候補から除いて復旧操作を返す。"""
    path = tmp_path / invalid
    if invalid == "directory":
        path.mkdir()
    elif invalid == "malformed":
        path.write_text("invalid content", encoding="utf-8")
    elif invalid == "empty-file":
        path.write_text("", encoding="utf-8")
    elif invalid == "comments-only":
        path.write_text("# APIキーは未記入\n\n", encoding="utf-8")
    elif invalid == "unreadable":

        def denied(_path: str) -> dict[str, str]:
            raise PermissionError("権限なし")

        monkeypatch.setattr(settings, "read_api_env_file", denied)
    _configure(monkeypatch, credits_file=str(path))
    choices = connection.candidates("delegate")
    assert [item.kind for item in choices.connections] == ["subscription"]
    assert str(path) in choices.warnings[0]
    assert "atk config set claude_credits_env_file" in choices.warnings[0]


@pytest.mark.parametrize(
    ("saved", "order", "expected"),
    [
        # 保持した種別が使用順に含まれる間は、その接続を使い続ける。
        ("subscription", "credits,subscription", "subscription"),
        ("credits", "credits,subscription", "credits"),
        # 使用順から外した種別は、使用順の先頭の利用可能な候補へ移す（旧api-only相当）。
        ("subscription", "payg", "payg"),
        ("credits", "subscription,payg", "subscription"),
        # 接続を管理する前に開始したinheritの会話も、使用順の種別ではないため先頭の候補へ移す。
        ("inherit", "credits,subscription", "credits"),
    ],
)
def test_saved_connection_moves_only_when_outside_order(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, saved: str, order: str, expected: str
) -> None:
    credits_file = _write_env(tmp_path / "credits.env")
    payg = _write_env(tmp_path / "payg.env")
    _configure(monkeypatch, credits_file=credits_file, payg=payg, worker_order=order)
    path = {"credits": credits_file, "payg": payg}.get(saved)
    resolved = connection.apply_current_settings(connection.Connection(saved, path), role="delegate")
    assert resolved.kind == expected
    assert connection.outside_order(saved, "delegate") is (saved != expected)


@pytest.mark.parametrize(("path_name", "expected"), [("credits.env", "credits"), ("other.env", "payg")])
def test_legacy_api_connection_resumes_with_saved_env_file(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, path_name: str, expected: str
) -> None:
    """種別を区別する前に保存した`api`の会話は、保存済みのenvファイルのパスで同じ接続を続ける。"""
    credits_file = _write_env(tmp_path / "credits.env")
    saved = _write_env(tmp_path / path_name)
    _configure(monkeypatch, credits_file=credits_file, worker_order="credits,payg,subscription")
    resolved = connection.apply_current_settings(connection.Connection("api", saved), role="delegate")
    assert (resolved.kind, resolved.api_env_file) == (expected, saved)


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
def test_subscription_login_without_environment_token(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
) -> None:
    """ファイル保存とKeychainをCLIが解決するmacOSのログインを認証ありとして扱う。"""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CUSTOM_CONNECTION", "parent")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    credentials = tmp_path / ".credentials.json"
    credentials.write_text(
        json.dumps({"claudeAiOauth": {"accessToken": "test-login", "refreshToken": "test-refresh"}}), encoding="utf-8"
    )
    calls: list[list[str]] = []

    def status(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert kwargs["env"]["CLAUDE_CONFIG_DIR"] == str(tmp_path)
        assert "CUSTOM_CONNECTION" not in kwargs["env"]
        assert "CUSTOM_CONNECTION" not in json.loads(argv[argv.index("--settings") + 1])["env"]
        return subprocess.CompletedProcess(argv, 0, json.dumps({"authMethod": "claude.ai", "loggedIn": True}))

    monkeypatch.setattr(subprocess, "run", status)
    assert settings.subscription_available(platform=platform, isolation_keys=settings.AUTH_ENV | {"CUSTOM_CONNECTION"})
    assert bool(calls) is (platform == "darwin")


def test_process_loop_reclaims_settings_when_session_start_log_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """起動前のログ失敗でも、作成済みの対話起動設定を回収する。"""
    with cast(
        settings.SettingsFile, settings.merge_settings(None, {"CUSTOM_CONNECTION": "value"}, None, force_file=True)
    ) as owned:
        monkeypatch.setattr(process_loop_session, "_build_session_argv", lambda *_args, **_kwargs: (["claude", owned], None))

        def failed_log(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("log failed")

        monkeypatch.setattr(process_loop_session._process_loop_log, "append", failed_log)  # pylint: disable=protected-access
        with pytest.raises(RuntimeError, match="log failed"):
            process_loop_session.run_process_session(
                argparse.Namespace(no_update=True),
                tmp_path,
                "調査",
                {},
                orchestrator="claude",
                model="model",
                effort="low",
                resume_pending=False,
            )
        assert not pathlib.Path(owned).exists()


@pytest.mark.parametrize("failure_stage", ["debug", "registry"])
def test_process_loop_preparation_failure_leaves_no_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, failure_stage: str
) -> None:
    """argvの準備が失敗した場合も、後続が回収できない設定ファイルを残さない。"""
    api = tmp_path / "api.env"
    api.write_text("ANTHROPIC_API_KEY=key\nCUSTOM_CONNECTION=selected\n", encoding="utf-8")
    monkeypatch.setattr(
        connection, "candidates", lambda *_args, **_kwargs: connection.Candidates((connection.Connection("payg", str(api)),))
    )

    def failed_debug(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError(f"{failure_stage} failed")

    if failure_stage == "debug":
        monkeypatch.setattr(process_loop_env, "create_hook_debug_log", failed_debug)
    else:
        monkeypatch.setattr(process_loop_session._registry, "publish", failed_debug)  # pylint: disable=protected-access
    with pytest.raises(RuntimeError, match=f"{failure_stage} failed"):
        process_loop_session.run_process_session(
            argparse.Namespace(no_update=True, resume=None),
            tmp_path,
            "調査",
            {},
            orchestrator="claude",
            model="model",
            effort="low",
            resume_pending=False,
        )
    assert not list(state_paths.state_dir().glob("agents-server/**/settings-*.json"))


def test_probe_settings_are_reclaimed_on_interrupt(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """probeの子実行が中断しても、API接続の合成設定を回収する。"""
    api = tmp_path / "api.env"
    api.write_text("ANTHROPIC_API_KEY=key\nCUSTOM_CONNECTION=selected\n", encoding="utf-8")
    monkeypatch.setattr(
        connection, "candidates", lambda *_args, **_kwargs: connection.Candidates((connection.Connection("payg", str(api)),))
    )
    monkeypatch.setattr(orchestrator, "_probe_model", lambda *_args: "model")
    captured: list[pathlib.Path] = []

    def interrupted(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append(pathlib.Path(argv[argv.index("--settings") + 1]))
        assert captured[0].is_file()
        assert kwargs["env"]["CUSTOM_CONNECTION"] == "selected"
        raise KeyboardInterrupt

    monkeypatch.setattr(subprocess, "run", interrupted)
    with pytest.raises(KeyboardInterrupt):
        orchestrator.select_available(
            [("claude", "model", "low")],
            {orchestrator.PROCESS_LOOP_SESSION_ENV: "1"},
            tmp_path,
            orchestrator.ProbeContext("test", "調査"),
        )
    assert captured
    assert not captured[0].exists()
