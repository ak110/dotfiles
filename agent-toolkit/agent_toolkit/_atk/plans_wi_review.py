import os
import pathlib
import re

from agent_toolkit._atk import git_sync, plans_bundle, plans_storage
from agent_toolkit._atk.wi.web_input import WebInputError
from agent_toolkit._common.private_notes import default_private_notes
from agent_toolkit._plan import bundle_kinds, commit_mapping, locations

_NAME = re.compile(r"wi-([0-9a-f]{7,64})-([0-9]{8}-[0-9]{6}-[0-9]{3})\.exec-review\.tsv")


def saved_wi_review(path: pathlib.Path) -> bool:
    return (
        re.fullmatch(r"\d{4}/(?:0[1-9]|1[0-2])", path.parent.as_posix()) is not None and _NAME.fullmatch(path.name) is not None
    )


def archive_lane_review(initial_head: str, wis: list[str], handoff: pathlib.Path) -> None:
    if not wis:
        return
    root = locations.working_plans_root()
    first = pathlib.Path(min(wis)).stem
    tables = []
    for path in root.glob("wi-*.exec-review.tsv"):
        match = _NAME.fullmatch(path.name)
        if match and match[2] == first and initial_head.startswith(match[1]):
            tables.append(path)
    if not tables:
        return
    if len(tables) != 1:
        raise ValueError(f"保存対象のレビュー表が一意ではありません: {tables}")
    table = tables[0]
    record = table.with_name(bundle_kinds.WI_COMMITS.name_for(table.name.removesuffix(bundle_kinds.EXEC_REVIEW.suffix)))
    source_record = record if record.exists() or record.is_symlink() else commit_mapping.mapping_path(handoff)
    sources = (table, source_record) if source_record.exists() or source_record.is_symlink() else (table,)
    if any(path.is_symlink() or not path.is_file() for path in sources):
        raise ValueError(f"レビュー表と対応記録は通常ファイルにしてください: {sources}")
    year, month, _day = plans_storage.birth_date(table).split("/")
    relative = pathlib.Path(year, month, table.name)
    notes = default_private_notes()
    with git_sync.repo_lock(notes):
        if git_sync.has_remote(notes):
            git_sync.push_pending_commits(notes)
            git_sync.pull(notes)
        saved, snapshots = plans_storage.copy_working_bundle(
            notes, relative, sources, destination_names={source_record: record.name}
        )
        git_sync.commit_and_push(
            notes, f"chore: update WI review {first}", tuple(plans_bundle.as_relative_notes_path(path, notes) for path in saved)
        )
        if git_sync.has_remote(notes) and not git_sync.remote_contains_head(notes):
            raise WebInputError(
                "レビュー表のcommitがremote branchへ到達していません", next_action=plans_bundle.UNPUSHED_NEXT_ACTION
            )
        plans_storage.remove_checked_out_working_bundle(
            tuple(source for source in sources if source != commit_mapping.mapping_path(handoff)),
            snapshots,
            pathlib.Path(table.name),
        )


def checkout_wi_review(
    notes: pathlib.Path, relative: pathlib.Path, home: pathlib.Path | str | None
) -> tuple[pathlib.Path, ...]:
    saved_root = locations.new_plans_root(notes)
    saved = saved_root / relative
    if not saved.resolve().is_relative_to(saved_root.resolve()):
        raise WebInputError(
            "レビュー表のパスが`private-notes/plans/`の外です", next_action="保存済みの年月とファイル名を指定する"
        )
    record = saved.with_name(bundle_kinds.WI_COMMITS.name_for(saved.name.removesuffix(bundle_kinds.EXEC_REVIEW.suffix)))
    root = locations.working_plans_root(home)
    with git_sync.repo_lock(notes):
        if git_sync.has_remote(notes):
            git_sync.push_pending_commits(notes)
            git_sync.pull(notes)
        sources = (saved, record) if record.exists() or record.is_symlink() else (saved,)
        if any(path.is_symlink() or not path.is_file() for path in sources):
            raise WebInputError(
                f"保存済みレビュー表か対応記録を読めません: {relative}", next_action="保存済みの年月とファイル名を指定する"
            )
        root.mkdir(parents=True, exist_ok=True)
        copied: list[pathlib.Path] = []
        try:
            for source in sources:
                content = source.read_bytes()
                destination = root / source.name
                descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                copied.append(destination)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(content)
        except Exception:
            for path in copied:
                path.unlink()
            raise
        return tuple(copied)
