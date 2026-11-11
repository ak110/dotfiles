"""run-skill報告の同期確認と原稿冒頭の比較・表示。"""

import pathlib
import re
import sys

from agent_toolkit._atk import output_file as _output_file
from agent_toolkit._atk.managed_temp.creation import create_output_temp
from agent_toolkit._atk.managed_temp.errors import ManagedTempError
from agent_toolkit._atk.wi import frontmatter as _frontmatter
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi import uwi as _uwi
from agent_toolkit._atk.wi.constants import WI_TYPE_UWI
from agent_toolkit._atk.wi.layout import iter_entry_paths
from agent_toolkit._atk.wi.web_input import WebInputError
from agent_toolkit._common import markdown_headings as _markdown_headings
from agent_toolkit._git import command as _git_command
from agent_toolkit._git import refs as _git_refs


def has_synced_run_report(private_notes: pathlib.Path, *, run_id: str, skill: str) -> bool:
    """当該実行の報告UWIが、保存本文と同じ内容でcommit・必要なpushへ到達したかを返す。"""
    if not private_notes.is_dir():
        return False
    with _wi_sync.repo_lock(private_notes, timeout=30):
        remote_revision: str | None = None
        if _wi_sync.has_remote(private_notes):
            branch = _git_refs.resolve_branch(private_notes, git_fn=lambda path, args: _git_command.output(args, path))
            if branch is None:
                return False
            remote = _git_command.output(["config", f"branch.{branch.branch}.remote"], private_notes).strip()
            merge_ref = _git_command.output(["config", f"branch.{branch.branch}.merge"], private_notes).strip()
            remote_tip = _git_command.run(
                ["ls-remote", "--exit-code", remote, merge_ref],
                private_notes,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout.split()
            if not remote_tip:
                return False
            remote_revision = remote_tip[0]
            known = _git_command.run(
                ["cat-file", "-e", f"{remote_revision}^{{commit}}"],
                private_notes,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if known.returncode != 0:
                _git_command.run(
                    ["fetch", "--no-tags", remote, merge_ref],
                    private_notes,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
        for path in iter_entry_paths(private_notes):
            if path.is_file():
                content = path.read_text(encoding="utf-8")
                parsed = _frontmatter.parse_frontmatter(content)
                if parsed is None:
                    continue
                data, _body = parsed
                if not (
                    data.get("type") == WI_TYPE_UWI
                    and data.get("source") == "run-skill"
                    and data.get("scope") == skill
                    and data.get("run_skill_report") == "true"
                    and data.get("run_skill_run_id") == run_id
                ):
                    continue
                relative = path.relative_to(private_notes).as_posix()
                committed = _git_command.run(
                    ["-C", str(private_notes), "show", f"HEAD:{relative}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                report_content = _run_report_content(content)
                if committed.returncode != 0 or _run_report_content(committed.stdout) != report_content:
                    continue
                if remote_revision is None:
                    return True
                # 回答と採否のcommitを除き、現在の報告本文を保存した最新の変更点を探す。
                # 同じ本文の過去版へ戻した場合も、戻したcommitのpushを必要とする。
                commit = ""
                for revision in _git_command.output(["log", "--format=%H", "--", relative], private_notes).splitlines():
                    historical = _git_command.run(
                        ["show", f"{revision}:{relative}"], private_notes, check=True, capture_output=True, text=True
                    ).stdout
                    if _run_report_content(historical) != report_content:
                        break
                    commit = revision
                if not commit:
                    continue
                reached = _git_command.run(
                    ["-C", str(private_notes), "merge-base", "--is-ancestor", commit, remote_revision],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if reached.returncode == 0:
                    return True
    return False


def _run_report_content(content: str) -> tuple[dict[str, object], str] | None:
    """報告本体を、保存後に追加される回答・採否から区別する。"""
    parsed = _frontmatter.parse_frontmatter(content)
    if parsed is None:
        return None
    data, body = parsed
    # answer_uwiと同じ最後のマーカーを境界にし、回答後の処理結果も除く。
    return {key: value for key, value in data.items() if key != "state"}, body.rsplit(_uwi.ANSWER_MARKER, 1)[0]


def compare_draft_openings(
    messages: list[str], sources: list[str], *, compare_paths: list[str] | None = None
) -> list[tuple[str, str]]:
    """原稿ごとの冒頭を抽出し、比較入力があれば全件の一致を確かめて`(原稿, 冒頭)`を返す。

    dry-runと本保存が同じ比較を使う。不一致・読取不能・件数違いは`WebInputError`で拒否し、呼出側は保存を始めない。
    """
    if compare_paths is not None and len(compare_paths) != len(messages):
        raise WebInputError(
            f"原稿と冒頭比較ファイルの件数が一致しない: 原稿={len(messages)}、比較={len(compare_paths)}",
            next_action="--body-fileと--compare-opening-fileを同じ件数、同じ対応順で指定して再実行する",
        )
    openings: list[tuple[str, str]] = []
    for index, (message, source) in enumerate(zip(messages, sources, strict=True)):
        raw = message.removeprefix("\ufeff")
        normalized = _frontmatter.normalize_newlines(raw)
        parsed = _frontmatter.parse_frontmatter(normalized)
        lines = re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", raw)
        if parsed is not None:
            prefix = normalized[: len(normalized) - len(parsed[1])]
            lines = lines[prefix.count("\n") :]
        body = "".join(lines)
        headings = [(token, name) for token, name in _markdown_headings.parse_headings(body, 1) if token.level == 0]
        start = headings[0][0].map[0] if headings and headings[0][0].map is not None else 0
        end = next(
            (
                token.map[0]
                for token, _name in _markdown_headings.parse_headings(body, 2)
                if token.level == 0 and token.map is not None and token.map[0] > start
            ),
            len(lines),
        )
        opening = "".join(lines[start:end])
        if compare_paths is not None:
            comparison = pathlib.Path(compare_paths[index]).expanduser()
            try:
                expected = comparison.read_bytes().decode("utf-8")
            except (OSError, UnicodeDecodeError) as error:
                raise WebInputError(
                    f"冒頭比較ファイルをUTF-8として読めない: {comparison}（{error}）",
                    next_action="--compare-opening-fileのパス・読取権限・UTF-8の文字コードを確認して再実行する",
                ) from error
            if opening != expected:
                raise WebInputError(
                    f"原稿の冒頭と比較ファイルが一致しない: 原稿={source}、比較={comparison}",
                    next_action=(
                        "--compare-opening-fileを外して現行原稿でdry-runを実行し、"
                        "返された冒頭保存先を比較ファイルとして指定し直す"
                    ),
                )
        openings.append((source, opening))
    return openings


def print_draft_openings(messages: list[str], sources: list[str], *, compare_paths: list[str] | None = None) -> None:
    """検証済み原稿の冒頭を比較・保存し、入力との対応と原文を表示する。"""
    saved: list[tuple[str, pathlib.Path, str]] = []
    try:
        for source, opening in compare_draft_openings(messages, sources, compare_paths=compare_paths):
            path = _output_file.save_text(opening, lambda: create_output_temp("wi-draft"), filename="draft-opening.md")
            saved.append((source, path, opening))
    except (OSError, ManagedTempError) as error:
        raise WebInputError(
            f"原稿冒頭の一時保存に失敗した: {error}",
            next_action="managed-tempの権限・空き容量を確認し、同じ原稿でdry-runを再実行する",
        ) from error
    # 全件の保存が成立するまで、有効な抽出として表示しない。
    for source, path, opening in saved:
        print(f"原稿: {source}\n冒頭保存先: {path}\n冒頭文字数: {len(opening)}\n冒頭本文:")
        sys.stdout.write(opening)
        print("\n冒頭本文終了")
