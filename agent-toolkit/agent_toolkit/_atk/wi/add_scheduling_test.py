# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""WIのadd: schedulingのテスト。"""

# pylint: disable=protected-access,subprocess-run-check

import contextlib
import datetime
import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import add as add_module
from agent_toolkit._atk.wi import frontmatter, readiness
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi.web_input import WebInputError
from agent_toolkit._testing.wi_add_support import _cmd_add_args, _patch_cmd_add_operations, _run_public_add
from agent_toolkit._testing.wi_add_support import _isolate_managed_temp as _isolate_managed_temp
from agent_toolkit.atk_test import _FIXED_DT, _setup_notes


def test_add_operation_does_not_infer_plan_file_from_body(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本文が実在する計画ファイルへ言及しても、`plan_file`と`queue_schedule`を自動で付けない。"""
    notes = tmp_path / "private-notes"
    (notes / "2024" / "01").mkdir(parents=True)
    plan = tmp_path / "plan.md"
    plan.write_text("# plan\n", encoding="utf-8")
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)

    generated = add_module.add_entries(
        notes,
        messages=[f"対象計画ファイル: `{plan}`"],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
    )

    content = (notes / "2024" / "01" / generated[0]).read_text(encoding="utf-8")
    parsed = frontmatter.parse_frontmatter(content)
    assert parsed is not None
    assert "queue_schedule" not in parsed[0]
    assert "plan_file" not in parsed[0]


def test_add_operation_records_top_level_dependencies(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """depends_on指定をトップレベル配列として重複なく記録する。"""
    notes = tmp_path / "private-notes"
    (notes / "2024" / "01").mkdir(parents=True)
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)

    generated = add_module.add_entries(
        notes,
        messages=["本文"],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
        depends_on=("first.md", "second.md"),
    )

    parsed = frontmatter.parse_frontmatter((notes / "2024" / "01" / generated[0]).read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0]["depends_on"] == ["first.md", "second.md"]


def test_add_warns_for_missing_dependency_and_keeps_registering(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """実在しない依存先を指定しても投入を拒否せず、警告を出力したうえで登録を完了する。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setattr(add_module, "resolve_add_target", lambda _value: ("github.com/example/repo", None))
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)

    _run_public_add(_cmd_add_args(tmp_path, "本文", depends_on=["absent.md"]), notes, _FIXED_DT, tmp_path)

    captured = capsys.readouterr()
    generated = sorted(path.name for path in notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md") if path.suffix == ".md")
    assert len(generated) == 1
    warning, next_action = captured.err.splitlines()
    assert warning == f"警告: {generated[0]}のdepends_onが参照するabsent.mdは取り込み先に実在しません"
    assert next_action.startswith("次の操作: 登録は完了した。")
    assert "atk wi set-dependencies" in next_action
    assert "成功: 1件をinboxへ投入した" in captured.out


def test_add_does_not_warn_for_existing_dependency(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """依存先が取り込み先に実在する場合は警告を出力しない。"""
    notes = _setup_notes(tmp_path)
    (notes / "2024" / "01").mkdir(parents=True, exist_ok=True)
    (notes / "2024" / "01" / "present.md").write_text("---\nstate: inbox\ntype: awi\n---\n\n本文\n", encoding="utf-8")
    monkeypatch.setattr(add_module, "resolve_add_target", lambda _value: ("github.com/example/repo", None))
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)

    _run_public_add(_cmd_add_args(tmp_path, "本文", depends_on=["present.md"]), notes, _FIXED_DT, tmp_path)

    assert capsys.readouterr().err == ""


def test_add_cli_dependencies_are_validated_and_normalized(tmp_path: pathlib.Path) -> None:
    """`add` CLIの依存を単純ファイル名へ限定し、拡張子省略と重複を正規化する。"""
    inbox = tmp_path / "2024" / "01"
    inbox.mkdir(parents=True)

    assert add_module._normalize_dependencies(["first", "first.md", "second.md"], inbox) == (  # pylint: disable=protected-access  # noqa: SLF001
        "first.md",
        "second.md",
    )
    with pytest.raises(SystemExit) as exc_info:
        add_module._normalize_dependencies(["../outside"], inbox)  # pylint: disable=protected-access  # noqa: SLF001
    assert exc_info.value.code == 2


def test_add_rejects_dependencies_for_uwi(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """UWIで保存されないdepends_on指定を成功扱いにしない。"""
    _setup_notes(tmp_path)
    body_path = tmp_path / "body.md"
    body_path.write_text("確認しますか？", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        atk.main(
            [
                "wi",
                "add",
                "--target-repo",
                "github.com/example/repo",
                "--type=uwi",
                "--question-type=yes-no",
                "--depends-on",
                "awi.md",
                "--body-file",
                str(body_path),
            ],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert exc_info.value.code == 1
    assert "--type=awiでのみ" in capsys.readouterr().err


@pytest.mark.parametrize("deadline", [None, "2026-01-02T03:04:05Z", "2026-01-02T12:04:05+09:00"])
def test_public_add_cooldown_first_save_and_readiness(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], deadline: str | None
) -> None:
    """公開CLIの複数本文が初回から同じ期限を持ち、期限ちょうどに着手可能となる。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    args = _cmd_add_args(tmp_path, "1件目")
    second = tmp_path / "second.md"
    second.write_text("2件目", encoding="utf-8")
    argv = ["wi", "add", "--target-repo", args.target_repo, "--body-file", args.body_file[0], "--body-file", str(second)]
    if deadline is not None:
        argv.extend(("--cooldown-until", deadline))
    with pytest.raises(SystemExit) as finished:
        atk.main(argv, home=tmp_path, now=_FIXED_DT)
    assert finished.value.code == 0
    paths = sorted(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
    assert len(paths) == 2
    expected = datetime.datetime.fromisoformat(deadline).isoformat() if deadline else None
    for path in paths:
        parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
        assert parsed is not None
        assert parsed[0].get("cooldown_until") == expected
    output = capsys.readouterr().out
    assert f"cooldown_until: {expected or 'なし'}" in output
    boundary = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.UTC)
    names = tuple(path.name for path in paths)
    for delta in (-1, 0, 1):
        result = readiness.calculate_readiness(notes, args.target_repo, now=boundary + datetime.timedelta(seconds=delta))
        assert result.ready == (() if deadline and delta < 0 else names)
        assert result.cooldown_pending == (names if deadline and delta < 0 else ())


def test_public_add_legacy_confirmation_is_saved_without_blocking_readiness(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧確認キーを未知キーとして保存し、着手可否に影響させない。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    args = _cmd_add_args(tmp_path, "---\nconfirmation: required\n---\n\n本文\n")
    with pytest.raises(SystemExit) as finished:
        atk.main(
            ["wi", "add", "--target-repo", args.target_repo, "--body-file", args.body_file[0]], home=tmp_path, now=_FIXED_DT
        )
    assert finished.value.code == 0
    (path,) = notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md")
    parsed = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0]["confirmation"] == "required"
    result = readiness.calculate_readiness(notes, args.target_repo)
    assert result.ready == (path.name,)


@pytest.mark.parametrize(
    "extra_args,value",
    [
        ([], "confirmed"),
        ([], "yes"),
        (["--type=uwi", "--question-type=yes-no"], "required"),
    ],
)
def test_public_add_preserves_legacy_confirmation_values(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    extra_args: list[str],
    value: str,
) -> None:
    """AWIとUWIに指定した旧確認キーの値を未知キーとして保持する。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    args = _cmd_add_args(tmp_path, f"---\nconfirmation: {value}\n---\n\n本文\n")
    argv = ["wi", "add", "--target-repo", args.target_repo, "--body-file", args.body_file[0], *extra_args]
    with pytest.raises(SystemExit) as failed:
        atk.main(argv, home=tmp_path, now=_FIXED_DT)
    assert failed.value.code == 0
    capsys.readouterr()
    (saved,) = notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md")
    assert "confirmation:" in saved.read_text(encoding="utf-8")


@pytest.mark.parametrize("deadline", ["", "not-a-date", "2026-01-02", "2026-01-02T03:04:05"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_public_add_rejects_invalid_cooldown_without_saving(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], deadline: str, dry_run: bool
) -> None:
    """期限の空・構文不正・timezone欠落は通常投入とdry-runの双方で拒否する。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    args = _cmd_add_args(tmp_path, "本文")
    argv = ["wi", "add", "--target-repo", args.target_repo, "--body-file", args.body_file[0], "--cooldown-until", deadline]
    if dry_run:
        argv.append("--dry-run")
    with pytest.raises(SystemExit) as failed:
        atk.main(argv, home=tmp_path, now=_FIXED_DT)
    assert failed.value.code != 0
    assert "cooldown" in capsys.readouterr().err
    assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))


@pytest.mark.parametrize("reserved_key", ["type", "target_commit", "queue_schedule"])
@pytest.mark.parametrize("entrypoint", ["cli", "dry-run", "web"])
def test_registration_rejects_reserved_metadata_before_any_save(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    reserved_key: str,
    entrypoint: str,
) -> None:
    """CLIとWebからの登録で、後続本文の予約キーを拒否して先行本文も保存しない。"""
    from agent_toolkit._atk.serve import wi_operations  # pylint: disable=import-outside-toplevel

    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    args = _cmd_add_args(tmp_path, "正常本文")
    invalid = f"---\n{reserved_key}: forged\n---\n\n拒否対象本文\n"
    if entrypoint == "web":
        with pytest.raises(WebInputError, match=reserved_key) as rejected:
            wi_operations.Operations(notes).add(["正常本文", invalid], entry_type="awi", target_repo=args.target_repo)
        assert rejected.value.next_action
    else:
        second = tmp_path / "reserved.md"
        second.write_text(invalid, encoding="utf-8")
        argv = [
            "wi",
            "add",
            "--target-repo",
            args.target_repo,
            "--body-file",
            args.body_file[0],
            "--body-file",
            str(second),
        ]
        if entrypoint == "dry-run":
            argv.append("--dry-run")
        with pytest.raises(SystemExit) as rejected_cli:
            atk.main(argv, home=tmp_path, now=_FIXED_DT)
        assert rejected_cli.value.code != 0
        assert reserved_key in capsys.readouterr().err
    assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))


@pytest.mark.parametrize("mode", ["uwi", "batch"])
def test_public_add_rejects_cooldown_for_unsupported_mode(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mode: str
) -> None:
    """UWIとbatchには期限オプションを適用しない。"""
    notes = _setup_notes(tmp_path)
    _patch_cmd_add_operations(monkeypatch)
    args = _cmd_add_args(tmp_path, "本文")
    argv = ["wi", "add", "--body-file", args.body_file[0], "--cooldown-until", "2026-01-02T03:04:05Z"]
    argv.extend(["--batch"] if mode == "batch" else ["--type=uwi", "--question-type=yes-no", "--target-repo", args.target_repo])
    with pytest.raises(SystemExit) as failed:
        atk.main(argv, home=tmp_path, now=_FIXED_DT)
    assert failed.value.code != 0
    assert "cooldown" in capsys.readouterr().err
    assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))
