"""WIの共通処理のテストが共有する、private-notesへのAWI・UWIのファイルの書き込み。"""

import pathlib


def write_uwi(
    private_notes: pathlib.Path,
    filename: str,
    *,
    target_repo: str = "github.com/example/repo",
    question: str = "確認事項",
    answer: str = "",
    source: str | None = None,
) -> None:
    """テスト用UWIをinboxへ書き込む。"""
    inbox = private_notes / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    source_line = f"source: {source}\n" if source is not None else ""
    (inbox / filename).write_text(
        f"---\ntarget_repo: {target_repo}\ntype: uwi\n{source_line}---\n\n## 質問\n\n{question}\n\n## 回答\n\n{answer}",
        encoding="utf-8",
    )


def write_awi(
    private_notes: pathlib.Path,
    filename: str,
    *,
    depends_on: tuple[str, ...] = (),
    legacy_dependency: str | None = None,
    plan_file: pathlib.Path | None = None,
    state: str = "inbox",
    target_repo: str = "github.com/example/repo",
    cooldown_until: object | None = None,
) -> pathlib.Path:
    """着手可否用frontmatterを持つテスト用AWIを書き込む。"""
    directory = private_notes / state
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    lines = ["---", f"target_repo: {target_repo}", "type: awi"]
    if depends_on:
        lines.extend(("depends_on:", *(f"  - {value}" for value in depends_on)))
    if legacy_dependency is not None:
        lines.extend(("queue_schedule:", "  dependency:", *legacy_dependency.splitlines()))
    if plan_file is not None:
        lines.append(f"plan_file: {plan_file}")
    if cooldown_until is not None:
        if isinstance(cooldown_until, str):
            lines.append(f"cooldown_until: {cooldown_until!r}")
        else:
            lines.append(f"cooldown_until: {cooldown_until!r}".lower())
    lines.extend(("---", "", "本文", ""))
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
