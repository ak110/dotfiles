import datetime
import pathlib
import re

FIXTURE_UNDATED_DATE = datetime.date(2026, 10, 1)


def entry_path(
    private_notes: pathlib.Path,
    filename: str,
    *,
    state: str = "inbox",
    legacy_layout: bool = False,
    fallback_date: datetime.date = FIXTURE_UNDATED_DATE,
) -> pathlib.Path:
    match = re.match(r"(\d{4})(\d{2})\d{2}-", filename)
    year, month = match.groups() if match is not None else (f"{fallback_date.year:04d}", f"{fallback_date.month:02d}")
    directory = private_notes / state if legacy_layout else private_notes / year / month
    directory.mkdir(parents=True, exist_ok=True)
    return directory / filename


def write_uwi(
    private_notes: pathlib.Path,
    filename: str,
    *,
    target_repo: str = "github.com/example/repo",
    question: str = "確認事項",
    answer: str = "",
    source: str | None = None,
    state: str = "inbox",
    legacy_layout: bool = False,
) -> None:
    path = entry_path(private_notes, filename, state=state, legacy_layout=legacy_layout)
    source_line = f"source: {source}\n" if source is not None else ""
    state_line = "" if legacy_layout else f"state: {state}\n"
    path.write_text(
        f"---\ntarget_repo: {target_repo}\ntype: uwi\n{state_line}{source_line}---\n\n"
        f"## 質問\n\n{question}\n\n## 回答\n\n{answer}",
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
    legacy_layout: bool = False,
) -> pathlib.Path:
    path = entry_path(private_notes, filename, state=state, legacy_layout=legacy_layout)
    lines = ["---", f"target_repo: {target_repo}", "type: awi"]
    if not legacy_layout:
        lines.append(f"state: {state}")
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
