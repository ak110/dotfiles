"""Claude Codeの配布済み設定に、管理元で削除した項目が残るかを検査する。"""

import argparse
import json
import os
import pathlib
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator

from pytools._internal.update_claude_settings import effective_managed_settings, preview_managed_settings_update

_SETTINGS = pathlib.Path("share/claude_settings_json_managed.json")


class _GitUnavailable(RuntimeError):
    pass


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], capture_output=True, check=True, encoding="utf-8").stdout.strip()
    except OSError as error:
        raise _GitUnavailable(str(error)) from error


def _from_commit(commit: str, path: pathlib.Path, *, optional: bool) -> dict:
    if optional and not _git("ls-tree", "--full-tree", commit, "--", path.as_posix()):
        return {}
    location = f"{commit}:{path.as_posix()}"
    try:
        source = _git("show", location)
    except UnicodeError as error:
        raise ValueError(f"{location}: UTF-8として読み取れない") from error
    return _read_object(source, location)


def _from_worktree(path: pathlib.Path, *, optional: bool) -> dict:
    try:
        return _read_object(path.read_text(encoding="utf-8"), str(path))
    except FileNotFoundError:
        if optional:
            return {}
        raise
    except UnicodeError as error:
        raise ValueError(f"{path}: UTF-8として読み取れない") from error


def _read_object(source: str, location: str) -> dict:
    try:
        data = json.loads(source)
    except json.JSONDecodeError as error:
        raise ValueError(f"{location}: {error}") from error
    if not isinstance(data, dict):
        raise ValueError(f"{location}: JSONの最上位が辞書ではない")
    return data


def _hook_groups(settings: dict) -> Iterator[tuple[str, dict, str]]:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        event_path = f'$["hooks"][{json.dumps(event, ensure_ascii=False)}]'
        for group_index, group in enumerate(groups):
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                continue
            yield event, group, f"{event_path}[{group_index}]"


def _hook_identity(event: str, group: dict, hook: object) -> tuple[str, str, str]:
    context = json.dumps({key: value for key, value in group.items() if key != "hooks"}, sort_keys=True)
    return event, context, json.dumps(hook, sort_keys=True)


def _remaining_hooks(expected: dict, actual: dict) -> Iterator[tuple[str, str]]:
    expected_groups = list(_hook_groups(expected))
    actual_groups = list(_hook_groups(actual))
    extra = Counter(_hook_identity(event, group, hook) for event, group, _ in actual_groups for hook in group["hooks"])
    extra.subtract(_hook_identity(event, group, hook) for event, group, _ in expected_groups for hook in group["hooks"])

    unchanged = Counter((event, json.dumps(group, sort_keys=True)) for event, group, _ in expected_groups)
    for event, group, path in actual_groups:
        group_key = (event, json.dumps(group, sort_keys=True))
        if unchanged[group_key]:
            unchanged[group_key] -= 1
            continue
        for index, hook in enumerate(group["hooks"]):
            key = _hook_identity(event, group, hook)
            if extra[key] > 0:
                extra[key] -= 1
                yield f'{path}["hooks"][{index}]', "hook"


def _residue(before: object, intended: object, actual: object, path: str = "$") -> Iterator[tuple[str, str]]:
    if isinstance(before, dict) and isinstance(actual, dict):
        intended_dict = intended if isinstance(intended, dict) else {}
        for key, value in before.items():
            if key not in actual:
                continue
            child = f"{path}[{json.dumps(key, ensure_ascii=False)}]"
            if key not in intended_dict:
                yield child, "辞書キー"
            else:
                yield from _residue(value, intended_dict[key], actual[key], child)
    elif isinstance(before, list) and isinstance(intended, list) and isinstance(actual, list):
        for index, value in enumerate(actual):
            if value in before and value not in intended:
                yield f"{path}[{index}]", "配列要素"


def removals_left_after_update(before: dict, after: dict) -> list[tuple[str, str]]:
    original = preview_managed_settings_update({}, before)
    expected = preview_managed_settings_update({}, after)
    actual = preview_managed_settings_update(original, after)

    old_values, new_values, updated_values = [
        {key: value for key, value in settings.items() if key != "hooks"} for settings in (original, expected, actual)
    ]
    return [*_remaining_hooks(expected, actual), *_residue(old_values, new_values, updated_values)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        default=os.environ.get("CLAUDE_SETTINGS_BASE") or None,
        help="比較元commit。CLAUDE_SETTINGS_BASEも無い場合はHEADとorigin/developのmerge-base",
    )
    args = parser.parse_args()
    try:
        base = (
            _git("rev-parse", "--verify", "--end-of-options", f"{args.base}^{{commit}}")
            if args.base is not None
            else _git("merge-base", "HEAD", "refs/remotes/origin/develop")
        )
        before = _from_commit(base, _SETTINGS, optional=False)
        after = _from_worktree(_SETTINGS, optional=False)
        found = False
        for platform in ("posix", "win32"):
            override = _SETTINGS.with_suffix(f".{platform}.json")
            old_settings = effective_managed_settings(before, [_from_commit(base, override, optional=True)])
            new_settings = effective_managed_settings(after, [_from_worktree(override, optional=True)])
            for path, kind in removals_left_after_update(old_settings, new_settings):
                found = True
                print(
                    f"{platform}: 更新後の {path}（{kind}）に削除した項目が残る。"
                    "update_claude_settings.pyの対応する除去指定を確認する。",
                    file=sys.stderr,
                )
        return int(found)
    except subprocess.CalledProcessError as error:
        print(
            f"管理設定の比較元を取得できない: {error.stderr.strip() or error}。"
            "比較元のGit履歴を取得して--baseへコミットを指定する。",
            file=sys.stderr,
        )
        return 2
    except _GitUnavailable as error:
        print(f"Gitを起動できない: {error}。Gitの導入とPATHから起動できることを確認する。", file=sys.stderr)
        return 2
    except (OSError, ValueError) as error:
        print(f"管理設定を読み取れない: {error}。対象ファイルの配置・権限、UTF-8とJSONの構文を確認する。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
