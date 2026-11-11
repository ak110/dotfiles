# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""post_applyのintegrationの試験。"""

# pylint: disable=protected-access

import json
import os
import subprocess
import types
import typing
from pathlib import Path

import pytest
import pytilpack.jsonc

from pytools import post_apply
from pytools._internal import common, post_apply_outcome
from pytools._internal.post_apply_test_helpers import _isolate_default_steps, _make_step
from pytools._internal.post_apply_test_helpers import _isolate_sync_report as _isolate_sync_report
from pytools._internal.post_apply_test_helpers import _isolate_update_log as _isolate_update_log


@pytest.mark.parametrize("missing_root", [True, False])
def test_normal_main_root_failure_reports_recovery_before_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    sync_report_path: Path,
    missing_root: bool,
) -> None:
    """ルート解決に失敗した通常の起動処理は確認対象と再実行手段を示し、工程とログを開始しない。"""
    root = tmp_path / "dotfiles"
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: None if missing_root else root)

    def fail_git(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 128, "", "not a git repository")

    monkeypatch.setattr(common, "run_subprocess", fail_git)
    calls: list[str] = []
    _isolate_default_steps(monkeypatch, {"Gemini 設定": _make_step("gemini", calls)})
    with pytest.raises(SystemExit) as exc_info:
        post_apply.main([])
    assert exc_info.value.code == 2
    assert not calls
    assert not post_apply._UPDATE_LOG_PATH.exists()
    assert not sync_report_path.exists()
    captured = capsys.readouterr()
    (tmp_path / "root.stdout.txt").write_text(captured.out, encoding="utf-8")
    (tmp_path / "root.stderr.txt").write_text(captured.err, encoding="utf-8")
    assert not captured.out
    assert "git" in captured.err
    assert "status" in captured.err
    assert "dotfiles-post-apply" in captured.err
    if not missing_root:
        assert str(root) in captured.err
        assert "not a git repository" in captured.err


@pytest.mark.parametrize("failure_mode", ["outcome", "exception", "invalid"])
@pytest.mark.parametrize("log_available", [True, False])
def test_normal_main_reports_failure_and_runs_successor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pytestconfig: pytest.Config,
    capsys: pytest.CaptureFixture[str],
    failure_mode: str,
    log_available: bool,
    sync_report_path: Path,
) -> None:
    """通常の起動処理で失敗理由・次操作を表示し、実出力先へ記録して後続を完了する。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: pytestconfig.rootpath)
    log_path = post_apply._UPDATE_LOG_PATH
    if not log_available:
        log_path.mkdir()
    calls: list[str] = []

    def fail() -> post_apply_outcome.PostApplyOutcome:
        calls.append("failure")
        if failure_mode == "exception":
            raise RuntimeError("受入検証の例外")
        if failure_mode == "invalid":
            return typing.cast("post_apply_outcome.PostApplyOutcome", False)
        return post_apply_outcome.PostApplyOutcome(
            failure="受入検証の書込失敗", next_action="対象設定の権限を直してupdate-dotfilesを再実行する"
        )

    def successor() -> post_apply_outcome.PostApplyOutcome:
        assert calls == ["failure"]
        calls.append("successor")
        return post_apply_outcome.PostApplyOutcome(changed=True)

    _isolate_default_steps(monkeypatch, {"Claude Code plugin のインストール": fail, "Claude 設定": successor})
    with pytest.raises(SystemExit) as exc_info:
        post_apply.main(["--allow-non-canonical-root"])
    assert exc_info.value.code == 1
    assert calls == ["failure", "successor"]
    captured = capsys.readouterr()
    (tmp_path / "main.stdout.txt").write_text(captured.out, encoding="utf-8")
    (tmp_path / "main.stderr.txt").write_text(captured.err, encoding="utf-8")
    reason = {
        "outcome": "受入検証の書込失敗",
        "exception": "RuntimeError: 受入検証の例外",
        "invalid": "戻り値が PostApplyOutcome ではない",
    }[failure_mode]
    assert reason in captured.err
    assert "次の操作:" in captured.err
    assert "update-dotfiles" in captured.err
    assert "失敗 1 件" in captured.out
    if failure_mode == "outcome":
        assert "対象設定の権限を直して" in captured.err
    if log_available:
        assert str(log_path) in captured.err
        log_text = log_path.read_text(encoding="utf-8")
        assert reason in log_text
        assert "次の操作:" in log_text
        assert "post-apply終了: exit=1" in log_text
    else:
        assert "永続ログを開始できませんでした" in captured.err
        assert "この標準エラー" in captured.err
        assert f"永続ログ {log_path}" not in captured.err
        assert "永続ログ:" not in captured.err
    report = json.loads(sync_report_path.read_text(encoding="utf-8"))["post_apply"]
    assert report["failed"] == 1
    assert report["updated"] == 1


@pytest.mark.parametrize("platform", ["linux", "win32"])
@pytest.mark.parametrize("existing", [False, True])
def test_normal_main_applies_host_settings_and_preserves_user_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pytestconfig: pytest.Config,
    capsys: pytest.CaptureFixture[str],
    platform: str,
    existing: bool,
) -> None:
    """通常の起動処理のClaude・Gemini工程は未作成と既存設定を管理値へ収束させ、再適用でも値を保つ。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    # Gitの照会は実OSのまま行い、Claude更新処理だけへ対象OS値を与える。
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: pytestconfig.rootpath)
    monkeypatch.setattr(post_apply.update_claude_settings, "sys", type("_Platform", (), {"platform": platform}))
    claude_path = home / ".claude/settings.json"
    gemini_path = home / ".gemini/settings.json"
    monkeypatch.setattr(post_apply.update_claude_settings, "_SETTINGS_PATH", claude_path)
    monkeypatch.setattr(post_apply.update_claude_settings, "_CONFIG_PATH", home / ".claude.json")
    original_claude = {
        "outputStyle": "Explanatory",
        "includeGitInstructions": True,
        "model": "user-model",
        "mcpServers": {"custom": {"command": "custom-tool"}},
    }
    original_gemini = {
        "general": {"topicUpdateNarration": True, "userOption": "keep"},
        "experimental": {"topicUpdateNarration": True},
        "model": {"name": "user-model"},
        "security": {"auth": {"selectedType": "user-auth"}},
        "mcpServers": {"custom": {"command": "custom-tool"}},
    }
    if existing:
        for path, data in ((claude_path, original_claude), (gemini_path, original_gemini)):
            path.parent.mkdir()
            path.write_text("// ユーザー設定\n" + json.dumps(data), encoding="utf-8")
    allowed = {step.name: step.run for step in post_apply.DEFAULT_STEPS if step.name in {"Claude 設定", "Gemini 設定"}}
    assert set(allowed) == {"Claude 設定", "Gemini 設定"}
    _isolate_default_steps(monkeypatch, allowed)
    previous: dict[str, object] | None = None
    for application in range(2):
        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(["--allow-non-canonical-root"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        (tmp_path / f"host-{application}.stdout.txt").write_text(captured.out, encoding="utf-8")
        (tmp_path / f"host-{application}.stderr.txt").write_text(captured.err, encoding="utf-8")
        claude = pytilpack.jsonc.loads(claude_path.read_text(encoding="utf-8"))
        gemini = pytilpack.jsonc.loads(gemini_path.read_text(encoding="utf-8"))
        assert claude["outputStyle"] == "default"
        assert claude["includeGitInstructions"] is False
        assert gemini["general"]["topicUpdateNarration"] is False
        if existing:
            for key in ("model", "mcpServers"):
                assert claude[key] == original_claude[key]
            for key in ("experimental", "model", "security", "mcpServers"):
                assert gemini[key] == original_gemini[key]
            assert gemini["general"]["userOption"] == "keep"
        current = {"claude": claude, "gemini": gemini}
        if previous is not None:
            assert current == previous
        previous = current


def test_external_prune_is_not_repeated_inside_update_post_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    """外側ランチャーがpruneを担当する実行ではuv配下へpruneを追加しない。"""
    monkeypatch.setenv("DOTFILES_PRUNE", "1")
    monkeypatch.setenv("DOTFILES_PRUNE_OUTSIDE_UV", "1")
    monkeypatch.setattr(post_apply, "DEFAULT_STEPS", [])
    monkeypatch.setattr(post_apply.prune_installation, "run", lambda: pytest.fail("uv配下でpruneへ到達した"))
    steps, _ = post_apply.run()
    assert not steps


@pytest.mark.parametrize("platform", ["posix", "nt"])
def test_standalone_prune_entry_runs_on_both_platforms(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str) -> None:
    """単独の公開--pruneは両OSで通常工程の後に削除を1回行う。"""
    calls: list[str] = []

    def prune() -> post_apply_outcome.PostApplyOutcome:
        calls.append("prune")
        return post_apply_outcome.PostApplyOutcome()

    monkeypatch.setattr(post_apply, "os", types.SimpleNamespace(name=platform, environ=os.environ, getpid=os.getpid))
    monkeypatch.delenv("DOTFILES_PRUNE_OUTSIDE_UV", raising=False)
    monkeypatch.setattr(post_apply, "DEFAULT_STEPS", [])
    monkeypatch.setattr(post_apply, "_home_is_substituted", lambda: False)
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: tmp_path)
    monkeypatch.setattr(
        common, "run_subprocess", lambda cmd, **_kwargs: subprocess.CompletedProcess(cmd, 0, f"{tmp_path / '.git'}\n", "")
    )
    monkeypatch.setattr(post_apply.prune_installation, "run", prune)

    with pytest.raises(SystemExit) as exc_info:
        post_apply.main(["--prune"])

    assert exc_info.value.code == 0
    assert calls == ["prune"]


@pytest.mark.parametrize(
    "arguments", [["--mode", "minimal"], ["--prune"], ["--mode", "minimal", "--prune"], ["--prune-only-if-minimal"]]
)
def test_prune_only_invalid_arguments_preserve_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, arguments: list[str]
) -> None:
    """排他的な引数は設定保存やpruneの前に拒否する。"""
    configuration = tmp_path / "config.json"
    configuration.write_text('{"dotfiles_mode": "full"}\n', encoding="utf-8")
    monkeypatch.setattr(post_apply.dotfiles_mode, "configuration_path", lambda: configuration)
    monkeypatch.setenv(post_apply.dotfiles_mode.MODE_ENV, "full")
    monkeypatch.delenv("DOTFILES_PRUNE", raising=False)
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: tmp_path)
    monkeypatch.setattr(
        common, "run_subprocess", lambda cmd, **_kwargs: subprocess.CompletedProcess(cmd, 0, f"{tmp_path / '.git'}\n", "")
    )
    monkeypatch.setattr(post_apply.prune_installation, "run", lambda: pytest.fail("pruneへ到達した"))
    before = configuration.read_bytes()

    with pytest.raises(SystemExit) as exc_info:
        post_apply.main(["--prune-only", *arguments])

    assert exc_info.value.code == 2
    assert configuration.read_bytes() == before
    assert os.environ[post_apply.dotfiles_mode.MODE_ENV] == "full"
    assert "DOTFILES_PRUNE" not in os.environ


@pytest.mark.parametrize("failure", [None, "削除に失敗した"])
def test_prune_only_entry_runs_only_prune_and_propagates_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], failure: str | None
) -> None:
    """正規rootのprune-onlyは通常の更新工程を起動せず削除結果を返す。"""
    calls: list[str] = []

    def prune() -> post_apply_outcome.PostApplyOutcome:
        calls.append("prune")
        return post_apply_outcome.PostApplyOutcome(failure=failure, next_action="権限を確認する" if failure else None)

    monkeypatch.setattr(common, "find_dotfiles_root", lambda: tmp_path)
    monkeypatch.setattr(
        common, "run_subprocess", lambda cmd, **_kwargs: subprocess.CompletedProcess(cmd, 0, f"{tmp_path / '.git'}\n", "")
    )
    monkeypatch.setattr(post_apply, "run", lambda: pytest.fail("通常の更新工程へ到達した"))
    monkeypatch.setattr(post_apply.prune_installation, "run", prune)

    if failure:
        with pytest.raises(SystemExit) as exc_info:
            post_apply.main(["--prune-only"])
        assert exc_info.value.code == 1
    else:
        post_apply.main(["--prune-only"])
    assert calls == ["prune"]
    captured = capsys.readouterr()
    assert not captured.out
    assert captured.err == (f"{failure}。次の操作: 権限を確認する\n" if failure else "")


@pytest.mark.parametrize(
    ("saved", "explicit", "prune", "expected"),
    [
        ("minimal", None, False, True),
        ("full", None, False, False),
        ("full", None, True, True),
        ("full", "minimal", False, True),
        ("minimal", "full", False, False),
        ("minimal", "full", True, True),
    ],
)
def test_prune_only_if_minimal_uses_effective_mode(  # noqa: PLR0913 -- 保存mode・明示指定・pruneの組合せを検証する
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, saved: str, explicit: str | None, prune: bool, expected: bool
) -> None:
    """更新後整理は保存modeと明示指定から実効modeを解決し、必要な場合だけpruneを実行する。"""
    configuration = tmp_path / "config.json"
    configuration.write_text(json.dumps({"dotfiles_mode": saved}), encoding="utf-8")
    monkeypatch.setattr(post_apply.dotfiles_mode, "configuration_path", lambda: configuration)
    monkeypatch.delenv(post_apply.dotfiles_mode.MODE_ENV, raising=False)
    calls: list[str] = []

    def run_prune(**_kwargs: object) -> post_apply_outcome.PostApplyOutcome:
        calls.append(post_apply.dotfiles_mode.resolve())
        return post_apply_outcome.PostApplyOutcome(changed=True)

    monkeypatch.setattr(post_apply.prune_installation, "run", run_prune)
    monkeypatch.setattr(post_apply, "run", lambda: pytest.fail("通常の更新工程へ到達した"))
    arguments = ["--prune-only-if-minimal"]
    if explicit is not None:
        arguments.extend(["--mode", explicit])
    if prune:
        arguments.append("--prune")
    post_apply.main(arguments, runner=lambda: pytest.fail("通常の更新工程へ到達した"))
    assert calls == ([explicit or saved] if expected else [])
