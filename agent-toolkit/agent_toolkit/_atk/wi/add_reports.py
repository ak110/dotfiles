"""run-skill報告の同期確認と、原稿冒頭の比較・表示、先送りの候補の表示。"""

import dataclasses
import pathlib
import re
import subprocess
import sys
from collections import Counter

from agent_toolkit._atk import outcome as _outcome
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
from agent_toolkit._common import skill_modes
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.diagnostic_redaction import redact_secrets
from agent_toolkit._git import command as _git_command
from agent_toolkit._git import refs as _git_refs


@dataclasses.dataclass(frozen=True)
class RunReportGap:
    """報告の同期を確認できなかった段階。  pyfltr: allow-comment

    `reason`は段階ごとの固定の区分で、失敗の識別に使える。
    `detail`は段階に固有の観測値（ファイル名、commitなど）で、失敗の調査に使う。
    """

    reason: str
    detail: dict[str, object]


_CANDIDATE_LIMIT = 5
"""報告用UWIが見つからない時に示す、近い候補の上限件数。  pyfltr: allow-comment"""


def has_synced_run_report(private_notes: pathlib.Path, *, run_id: str, scope: str) -> bool:
    """当該実行の起動単位`scope`の報告UWIが、保存本文と同じ内容でcommit・必要なpushへ到達したかを返す。

    `scope`と報告の`scope`は、どちらも`skill_modes.canonical_scope`でそろえて比べる。
    """
    return check_run_report_sync(private_notes, run_id=run_id, scope=scope) is None


def check_run_report_sync(
    private_notes: pathlib.Path, *, run_id: str, scope: str, sync_report: bool = False
) -> RunReportGap | None:
    """同期済みなら`None`、未同期なら確認できなかった段階を返す。  pyfltr: allow-comment

    条件に合う報告用UWIが複数あれば、最も同期に近い段階を返す。
    """
    expected = skill_modes.canonical_scope(scope)
    if not private_notes.is_dir():
        return RunReportGap("private-notesが無い", {"private_notes": str(private_notes)})
    with _wi_sync.repo_lock(private_notes, timeout=30):
        remote = merge_ref = ""
        remote_revision: str | None = None
        if _wi_sync.has_remote(private_notes):
            branch = _git_refs.resolve_branch(private_notes, git_fn=lambda path, args: _git_command.output(args, path))
            if branch is None:
                return RunReportGap("追跡ブランチを解決できない", {"private_notes": str(private_notes)})
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
                return RunReportGap("リモートの追跡先が無い", {"remote": remote, "merge_ref": merge_ref})
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
        gap: RunReportGap | None = None
        candidates: list[dict[str, object]] = []
        matched = 0
        unsaved: list[str] = []
        for path in iter_entry_paths(private_notes):
            if path.is_file():
                content = path.read_text(encoding="utf-8")
                parsed = _frontmatter.parse_frontmatter(content)
                if parsed is None:
                    continue
                data, _body = parsed
                saved_scope = data.get("scope")
                same_scope = isinstance(saved_scope, str) and skill_modes.canonical_scope(saved_scope) == expected
                if not (
                    data.get("type") == WI_TYPE_UWI
                    and data.get("source") == "run-skill"
                    and same_scope
                    and (data.get("run_skill_report") == "true" or bool(data.get("weekly_slot")))
                    and data.get("run_skill_run_id") == run_id
                ):
                    if (
                        data.get("type") == WI_TYPE_UWI
                        and data.get("source") == "run-skill"
                        and (same_scope or data.get("run_skill_run_id") == run_id)
                    ):
                        candidates.append(
                            {
                                "file": path.name,
                                **{
                                    key: data.get(key)
                                    for key in ("state", "scope", "run_skill_report", "run_skill_run_id", "weekly_slot")
                                },
                            }
                        )
                    continue
                matched += 1
                relative = path.relative_to(private_notes).as_posix()
                committed = _git_command.run(
                    ["-C", str(private_notes), "show", f"HEAD:{relative}"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                report_content = _run_report_content(content)
                if committed.returncode != 0 or _run_report_content(committed.stdout) != report_content:
                    if sync_report and not path.is_symlink():
                        unsaved.append(relative)
                    if gap is None:
                        gap = RunReportGap(
                            "報告用UWIがHEADのcommitと一致しない",
                            {"file": relative, "head": "未commit" if committed.returncode != 0 else "内容が異なる"},
                        )
                    continue
                if remote_revision is None:
                    return None

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
                    return None
                gap = RunReportGap(
                    "報告用UWIのcommitがリモートへ未到達",
                    {"file": relative, "commit": commit, "remote_revision": remote_revision},
                )
        repaired = matched == 1 and len(unsaved) == 1
        if repaired:
            if remote_revision is not None and _git_command.output(["rev-parse", "HEAD"], private_notes) != remote_revision:
                return RunReportGap("報告の回復を保留", {"理由": "HEADがリモート先端と異なるため、通常の同期が必要"})
            stage = "commit"
            try:
                _wi_sync.commit_and_push(private_notes, "chore: 振り返りbatchの報告を同期", unsaved, skip_push=True)
                if remote_revision is not None:
                    stage = "push"
                    head = _git_command.output(["rev-parse", "HEAD"], private_notes)
                    parents = _git_command.output(["rev-list", "--parents", "-n", "1", head], private_notes).split()
                    if parents != [head, remote_revision]:
                        return RunReportGap("報告の回復を保留", {"理由": "報告1件のcommitだけを確認できない"})
                    _git_command.run(
                        [
                            "push",
                            "--no-follow-tags",
                            "--recurse-submodules=no",
                            f"--force-with-lease={merge_ref}:{remote_revision}",
                            remote,
                            f"{head}:{merge_ref}",
                        ],
                        private_notes,
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                stderr = getattr(error, "stderr", None)
                if isinstance(stderr, bytes):
                    stderr = stderr.decode("utf-8", errors="replace")
                message = redact_secrets(stderr or str(error))[-2000:]
                return RunReportGap("報告の回復に失敗", {"段階": stage, "診断": message})
    if repaired:
        return check_run_report_sync(private_notes, run_id=run_id, scope=scope)
    if gap is not None:
        return gap
    candidates.sort(key=lambda item: str(item["file"]), reverse=True)
    return RunReportGap(
        "報告用UWIが無い",
        {"run_id": run_id, "scope": expected, "candidates": candidates[:_CANDIDATE_LIMIT], "candidate_count": len(candidates)},
    )


def _run_report_content(content: str) -> tuple[dict[str, object], str] | None:
    parsed = _frontmatter.parse_frontmatter(content)
    if parsed is None:
        return None
    data, body = parsed

    return {key: value for key, value in data.items() if key != "state"}, body.rsplit(_uwi.ANSWER_MARKER, 1)[0]


def compare_draft_openings(
    messages: list[str], sources: list[str], *, compare_paths: list[str] | None = None, is_uwi: bool = False
) -> list[tuple[str, str]]:
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
        if is_uwi:
            opening = _uwi.question_opening(body)
        else:
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


def print_draft_openings(
    messages: list[str],
    sources: list[str],
    *,
    compare_paths: list[str] | None = None,
    output_paths: list[str] | None = None,
    is_uwi: bool = False,
) -> None:
    saved: list[tuple[str, pathlib.Path, str]] = []
    try:
        openings = compare_draft_openings(messages, sources, compare_paths=compare_paths, is_uwi=is_uwi)
        targets = [pathlib.Path(path).expanduser().resolve() for path in output_paths or ()]
        if output_paths is not None:
            if len(targets) != len(openings) or len(set(targets)) != len(targets):
                raise WebInputError(
                    "冒頭抜粋の保存先が原稿と一対一で対応しない",
                    next_action="原稿と同じ件数・順序で異なる--opening-outputを指定する",
                )
            inputs = {pathlib.Path(path).expanduser().resolve() for path in [*sources, *(compare_paths or ())]}
            if inputs.intersection(targets):
                raise WebInputError(
                    "冒頭抜粋の出力先と原稿または比較入力が同じ",
                    next_action="作業用の--opening-outputを原稿・比較入力と異なるパスに指定する",
                )
        for index, (source, opening) in enumerate(openings):
            if targets:
                path = targets[index]
                atomic_write(path, opening)
            else:
                path = _output_file.save_text(opening, lambda: create_output_temp("wi-draft"), filename="draft-opening.md")
            saved.append((source, path, opening))
    except (OSError, ManagedTempError) as error:
        raise WebInputError(
            f"原稿冒頭の一時保存に失敗した: {error}",
            next_action="managed-tempの権限・空き容量を確認し、同じ原稿でdry-runを再実行する",
        ) from error

    for source, path, opening in saved:
        print(f"原稿: {source}\n冒頭保存先: {path}\n冒頭文字数: {len(opening)}\n冒頭本文:")
        sys.stdout.write(opening)
        print("\n冒頭本文終了")


DEFERRAL_PATTERN = re.compile(
    r"確かめて|確認して|比べて|比較して|照合して|計画で|計画工程|実装で|着手時|処理時|処理の時点|扱いを決め"
)
"""AWIの本文で、投入前に決められる判断を処理工程へ回す言い回しの候補。

正当な記述（登録時に観測できない事項を待つものなど）にも一致するため、拒否と警告には使わず、
投入前レビューが行ごとに判定する入力として示すだけにする。
"""

DEFERRAL_SECTIONS = frozenset({"反映内容と反映先", "完成条件"})
"""先送りの候補を探す節。処理担当が行う作業と完了の判定を書く節に限り、ユーザーの原文と観測の記録の節は除く。"""


def deferral_candidates(message: str) -> list[tuple[int, str]]:
    """AWIの原稿から先送りの候補の行を、原稿ファイル上の1始まりの行番号と行の本文の組で返す。"""
    normalized = _frontmatter.normalize_newlines(message.removeprefix("\ufeff"))
    parsed = _frontmatter.parse_frontmatter(normalized)
    offset = 0 if parsed is None else normalized[: len(normalized) - len(parsed[1])].count("\n")
    lines = normalized.split("\n")[offset:]
    headings = _markdown_headings.top_level_atx_headings("\n".join(lines), 2)
    candidates: list[tuple[int, str]] = []
    for index, (token, name) in enumerate(headings):
        if name.strip() not in DEFERRAL_SECTIONS or token.map is None:
            continue
        following = headings[index + 1][0].map if index + 1 < len(headings) else None
        end = following[0] if following is not None else len(lines)
        candidates.extend(
            (offset + number + 1, lines[number].strip())
            for number in range(token.map[1], end)
            if DEFERRAL_PATTERN.search(lines[number])
        )
    return candidates


def print_deferral_candidates(messages: list[str], sources: list[str], *, previous: str | None = None) -> None:
    """AWIの原稿ごとに先送りの候補を`情報:`の行で示す。候補が無い原稿は何も出さない。"""
    for message, source in zip(messages, sources, strict=True):
        candidates = deferral_candidates(message)
        if previous is not None:
            remaining = Counter(line for _, line in deferral_candidates(previous))
            new_candidates = []
            for number, line in candidates:
                if remaining[line]:
                    remaining[line] -= 1
                else:
                    new_candidates.append((number, line))
            candidates = new_candidates
        if not candidates:
            continue
        _outcome.report_info(
            f"先送りの候補が{len(candidates)}行ある（{source}）。保存の成否は変えない。"
            "投入前レビューが行ごとに、処理へ残してよい事項かを判定する"
        )
        for number, line in candidates:
            _outcome.report_info(f"先送りの候補 {number}行目: {line}")
