import argparse
import dataclasses
import pathlib
import sys

from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk.wi import attachments, background_sync
from agent_toolkit._atk.wi import branches as _wi_branches
from agent_toolkit._atk.wi import entries as _wi_entries
from agent_toolkit._atk.wi import frontmatter as _frontmatter
from agent_toolkit._atk.wi import sections as _wi_sections
from agent_toolkit._atk.wi import user_comment as _user_comment
from agent_toolkit._atk.wi import uwi as _uwi
from agent_toolkit._atk.wi import uwi_scan as _wi_uwi_scan
from agent_toolkit._atk.wi.constants import WI_TYPE_UWI, WI_TYPES
from agent_toolkit._atk.wi.formatters import (
    body_summary,
    uwi_body_summary,
)
from agent_toolkit._atk.wi.repo import resolve_repo_id
from agent_toolkit._common.markdown_headings import top_level_atx_headings
from agent_toolkit._git import remote as _git_remote


def user_field_location(text: str, kind: str | None) -> tuple[str, int] | None:
    section = _uwi.ANSWER_HEADING.removeprefix("## ") if kind == WI_TYPE_UWI else "ユーザーコメント"
    if kind != WI_TYPE_UWI and not _user_comment.has_reserved_heading(text):
        return None
    normalized = _frontmatter.normalize_newlines(text)
    parsed = _frontmatter.parse_frontmatter(normalized)
    body = parsed[1] if parsed is not None else normalized
    prefix_lines = normalized.count("\n") - body.count("\n")
    for token, content in top_level_atx_headings(body, 2):
        if content == section:
            assert token.map is not None
            return section, prefix_lines + token.map[0] + 1
    return None


@dataclasses.dataclass
class _ShowOutput:
    user_fields: list[str] | None
    line: int = 1
    without_field: int = 0

    def write(self, text: str = "") -> None:
        print(text)
        self.line += text.count("\n") + 1

    def body(self, name: str, text: str, kind: str | None, args: argparse.Namespace) -> None:
        """本文と記入欄の開始行を、表示する元の項目から同時に取得する。"""
        start = self.line
        self.write(_display_body(text, kind, args))
        if self.user_fields is None:
            return
        heading = _uwi.ANSWER_HEADING if kind == WI_TYPE_UWI else "## ユーザーコメント"
        field = user_field_location(text, kind)
        if field is None:
            self.without_field += 1
            return
        self.user_fields.append(f"記入欄: {name} {heading} あり 開始行: {start + field[1] - 1}")

    def attachment_list(self, private_notes: pathlib.Path, name: str) -> None:
        for path in attachments.paths(private_notes, name):
            self.write(f"添付: {path.name} ({path.relative_to(private_notes).as_posix()})")

    def finish(self) -> None:
        if self.user_fields is not None and self.without_field:
            self.user_fields.append(f"記入欄なし: {self.without_field}件")


def _summary_line(text: str, kind: str | None) -> str:
    if kind == WI_TYPE_UWI:
        return uwi_body_summary(text, sys.maxsize)
    summary = body_summary(text, sys.maxsize)
    return summary.removeprefix("# ") if summary.startswith("# ") else summary


def _display_body(text: str, kind: str | None, args: argparse.Namespace) -> str:
    if getattr(args, "summary_only", False):
        return _summary_line(text, kind)
    names = getattr(args, "section", None)
    if names is None:
        return text
    return "\n\n".join(f"#### {name}\n{body}" for name, body in _wi_sections.h2_section_items(text) if name in names)


def _report_missing(
    private_notes: pathlib.Path,
    filenames: list[tuple[str, str]],
    missing: list[str],
    repos: tuple[str, ...],
    args: argparse.Namespace,
) -> None:
    requested = [(name, normalized) for name, normalized in filenames if name in missing]
    entries, _ = _wi_entries.read_named_entries(
        private_notes, requested, target_repo=None, entry_type=args.type, source=args.source
    )
    stored_repos = {path.name: repo for path, repo, *_ in entries}
    for filename, normalized in requested:
        stored = stored_repos.get(normalized)
        if repos and stored is not None:
            canonical = _git_remote.canonical_repo(stored, {}) or stored
            _outcome.report_failure(
                f"対象リポジトリが一致しない: {filename}（指定値: {', '.join(repos)}、保存値: {canonical}）",
                next_action="保存値で--target-repoを指定し直すか、別リポジトリの項目か確かめる",
            )
        else:
            _outcome.report_failure(
                f"年月配置に存在しない: {filename}",
                next_action="実在するファイル名を指定し直す（`atk wi list`で候補を確認できる）",
            )


def cmd_show(args: argparse.Namespace, private_notes: pathlib.Path) -> None:
    output = _ShowOutput(getattr(args, "_saved_show_fields", None))
    if not args.filenames and not args.all:
        args.subparser.error("表示するファイル名または--allを指定してください。")
    validated_filenames = _wi_entries.validate_named_filenames(private_notes, args.filenames)
    background_sync.prepare_read(private_notes, skip=args.skip_pull, force=getattr(args, "pull", False))
    resolved_repos = tuple(dict.fromkeys(resolve_repo_id(repo) for repo in (args.target_repo or ())))
    branch_settings = _wi_branches.registry()

    if validated_filenames:
        explicit_repos = () if getattr(args, "target_repo_defaulted", False) else resolved_repos
        selected_by_name, missing = _wi_entries.read_named_entries(
            private_notes, validated_filenames, target_repo=explicit_repos, entry_type=args.type, source=args.source
        )
        if missing:
            _report_missing(private_notes, validated_filenames, missing, explicit_repos, args)
            sys.exit(2)
        for path, target_repo, text, state, kind in selected_by_name:
            label = f" [{state}]"
            if kind == WI_TYPE_UWI:
                label = f" [{state}/{_wi_uwi_scan.answer_label(text)}]"
            output.write(f"## target_repo: {target_repo}")
            output.write(f"### {path.name}{label}{_branch_label(text, target_repo, branch_settings)}")
            output.body(path.name, text, kind, args)
            output.attachment_list(private_notes, path.name)
            if len(selected_by_name) > 1:
                output.write()
        output.finish()
        return

    selected = _wi_entries.select_entries(
        private_notes,
        status=args.status,
        target_repo=resolved_repos or None,
        entry_type=args.type,
        answered=args.answered,
        source=args.source,
        important=getattr(args, "important", "all"),
        target_branch=_wi_branches.requested_filter(args, resolved_repos, branch_settings),
        branch_settings=branch_settings,
        indexed=True,
    )
    for header_type in WI_TYPES:
        entries: dict[str, list[tuple[str, str, str]]] = {}
        for path, target_repo, text, state, entry_type in selected:
            if entry_type != header_type:
                continue
            entries.setdefault(target_repo, []).append((path.name, text, state))
        if entries:
            output.write(f"# {header_type}")
            for repo, items in entries.items():
                output.write(f"## target_repo: {repo}")
                for name, text, state in items:
                    label = f" [{state}]"
                    if header_type == WI_TYPE_UWI:
                        label = f" [{state}/{_wi_uwi_scan.answer_label(text)}]"
                    output.write(f"### {name}{label}{_branch_label(text, repo, branch_settings)}")
                    output.body(name, text, header_type, args)
                    output.attachment_list(private_notes, name)
                    output.write()
    output.finish()


def _branch_label(text: str, target_repo: str, branch_settings: _wi_branches.Registry) -> str:
    branch = _wi_branches.effective(text, target_repo, branch_settings)
    return "" if branch is None else f" target_branch={branch}"
