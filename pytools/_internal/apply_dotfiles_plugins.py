"""本体の反映後にdotfilesプラグインの配布物と後処理を名前順に適用する。"""

import logging
import shutil
from pathlib import Path

import platformdirs

from pytools._internal import common, post_apply_outcome

logger = logging.getLogger(__name__)


def run() -> post_apply_outcome.PostApplyOutcome:
    """プラグインごとの失敗を集約し、残るプラグインの反映を続ける。"""
    root = common.find_dotfiles_root()
    if root is None:
        return _outcome(False, [], ["dotfilesのルートを解決できません"])
    plugins_root = root / "plugins"
    if not plugins_root.exists():
        return post_apply_outcome.PostApplyOutcome()
    plugins = sorted((path for path in plugins_root.iterdir() if path.is_dir()), key=lambda path: path.name)
    if not plugins:
        return post_apply_outcome.PostApplyOutcome()
    chezmoi = shutil.which("chezmoi")
    uv = common.resolve_uv_path()
    if chezmoi is None or uv is None:
        return post_apply_outcome.PostApplyOutcome(
            notices=(
                post_apply_outcome.PostApplyNotice(
                    "dotfilesプラグインをスキップしました。chezmoiとuvを導入してupdate-dotfilesを再実行してください。"
                ),
            ),
        )
    notices: list[post_apply_outcome.PostApplyNotice] = []
    failures: list[str] = []
    main_paths = _managed(chezmoi, root, []) if any((path / ".chezmoi-source").is_dir() for path in plugins) else set()
    if main_paths is None:
        return _outcome(False, notices, ["本体のchezmoi managedに失敗しました"])
    state_root = Path(platformdirs.user_state_dir("dotfiles", appauthor=False)) / "plugins"
    changed = False
    for plugin in plugins:
        try:
            changed = _apply_plugin(plugin, chezmoi, uv, state_root, main_paths, notices, failures) or changed
        except OSError as error:
            failures.append(f"{plugin.name}: {error}")
    return _outcome(changed, notices, failures)


def _apply_plugin(
    plugin: Path,
    chezmoi: str,
    uv: Path,
    state_root: Path,
    main_paths: set[str],
    notices: list[post_apply_outcome.PostApplyNotice],
    failures: list[str],
) -> bool:
    source = plugin / ".chezmoi-source"
    changed = False
    if source.is_dir():
        state = state_root / plugin.name / "chezmoistate.boltdb"
        state.parent.mkdir(parents=True, exist_ok=True)
        state_args = [f"--persistent-state={state}"]
        paths = _managed(chezmoi, source, state_args)
        if paths is None:
            failures.append(f"{plugin.name}: chezmoi managedに失敗しました")
            return False
        for target in sorted(main_paths & paths):
            notices.append(
                post_apply_outcome.PostApplyNotice(
                    f"dotfilesプラグイン {plugin.name} が本体の配布先 {target} を上書きします。"
                    "プラグインの内容を使う場合は対応不要です。重なりを解消する場合はプラグインの配布物を変更してください。"
                )
            )
        if not _execute(plugin, "apply", [chezmoi, "apply", "--force", f"--source={source}", *state_args], failures):
            return False
        changed = True
    script = plugin / "post_apply.py"
    if script.is_file():
        argv = [str(uv), "run", "--no-project", "--script", str(script)]
        changed = _execute(plugin, "post_apply.py", argv, failures) or changed
    return changed


def _managed(chezmoi: str, source: Path, state_args: list[str]) -> set[str] | None:
    result = common.run_subprocess(
        [
            chezmoi,
            "managed",
            "--path-style=absolute",
            "--include=files,symlinks",
            "--nul-path-separator",
            f"--source={source}",
            *state_args,
        ]
    )
    if result is None or result.returncode != 0:
        logger.error("chezmoi managed (%s): %s", source, common.format_cli_error(result))
        return None
    return set(result.stdout.split("\0")) - {""}


def _execute(plugin: Path, operation: str, argv: list[str], failures: list[str]) -> bool:
    result = common.run_subprocess(argv, cwd=plugin, env_overrides={"PYTHONIOENCODING": "utf-8:replace"})
    if result is not None:
        if result.stdout:
            logger.info("%s %s stdout:\n%s", plugin.name, operation, result.stdout)
        if result.stderr:
            logger.info("%s %s stderr:\n%s", plugin.name, operation, result.stderr)
    if result is None or result.returncode != 0:
        code = "起動失敗" if result is None else f"exit {result.returncode}"
        failures.append(f"{plugin.name}: {operation} ({code}): {common.format_cli_error(result)}")
        return False
    return True


def _outcome(
    changed: bool,
    notices: list[post_apply_outcome.PostApplyNotice],
    failures: list[str],
) -> post_apply_outcome.PostApplyOutcome:
    return post_apply_outcome.PostApplyOutcome(
        changed=changed,
        notices=tuple(notices),
        failure=" / ".join(failures) or None,
        next_action=(
            "表示されたプラグインの配布物・post_apply.pyとコマンド出力を確認し、"
            "原因を解消してupdate-dotfilesを再実行してください。"
            if failures
            else None
        ),
    )
