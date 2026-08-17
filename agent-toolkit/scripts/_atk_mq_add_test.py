"""atk (agent-toolkit `atk mq`) の`add`サブコマンド順序保証テスト。

エディター経由の本文確定後に`_pull`を実行しUXブロッキング待ちを最小化する順序
（エディター起動 → 本文確定 → `_pull` → 書込 → commit&push）が維持されていることを検証する。
基本動作テストは`atk_test.py`・`_atk_mq_extras_test.py`側に集約する。
共通ヘルパーは`_atk_git_fake_test_helpers.py`から再利用する。
"""

import contextlib
import pathlib
import subprocess
import sys
from collections.abc import Iterator
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_mq_add as add_module  # noqa: E402  # pylint: disable=wrong-import-position
import _atk_mq_frontmatter as frontmatter  # noqa: E402  # pylint: disable=wrong-import-position
import _atk_mq_tbd as tbd_module  # noqa: E402  # pylint: disable=wrong-import-position
import atk  # noqa: E402  # pylint: disable=wrong-import-position
from _atk_git_fake_test_helpers import (  # noqa: E402  # pylint: disable=wrong-import-position
    _FIXED_HEAD_COMMIT,
)
from _atk_git_fake_test_helpers import (  # noqa: E402  # pylint: disable=wrong-import-position
    fake_git_worktree_remote_response as _fake_git_worktree_remote_response,
)
from _atk_mq_common import MQ_TYPE_TBD, WebInputError  # noqa: E402  # pylint: disable=wrong-import-position
from atk_test import _FIXED_DT, _setup_notes  # noqa: E402  # pylint: disable=wrong-import-position


def test_flat_add_operation_is_public(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """平引数操作が生成名を返し、frontmatter付きファイルを書き込む。"""
    notes = tmp_path / "private-notes"
    (notes / "inbox").mkdir(parents=True)
    monkeypatch.setattr(add_module, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(add_module, "_pull", lambda _path: None)
    monkeypatch.setattr(add_module, "_commit_and_push", lambda *_args, **_kwargs: None)
    generated = add_module.add_entries(
        notes,
        messages=["本文"],
        target_repo="github.com/example/repo",
        source="test",
        now=_FIXED_DT,
    )
    assert generated == [f"{_FIXED_DT:%Y%m%d-%H%M%S}-001.md"]
    content = (notes / "inbox" / generated[0]).read_text(encoding="utf-8")
    assert "target_repo: github.com/example/repo" in content
    assert "source: test" in content


def test_add_reloads_saved_details_while_holding_lock(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """投入後の表示情報を排他ロック内で再読込する。"""
    notes = _setup_notes(tmp_path)
    lock_state = {"held": False}

    @contextlib.contextmanager
    def tracked_lock(*_args: object, **_kwargs: object) -> Iterator[None]:
        lock_state["held"] = True
        try:
            yield
        finally:
            lock_state["held"] = False

    original_read = add_module._read_saved_entry_details  # pylint: disable=protected-access  # noqa: SLF001

    def read_while_locked(path: pathlib.Path) -> dict[str, object | None]:
        assert lock_state["held"]
        return original_read(path)

    monkeypatch.setattr(add_module, "_repo_lock", tracked_lock)
    monkeypatch.setattr(add_module, "_pull", lambda _path: None)
    monkeypatch.setattr(add_module, "_commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(add_module, "_read_saved_entry_details", read_while_locked)
    saved_details: dict[str, dict[str, object | None]] = {}

    generated = add_module.add_entries(
        notes,
        messages=["本文"],
        target_repo="github.com/example/repo",
        source="test",
        now=_FIXED_DT,
        saved_details=saved_details,
    )

    assert saved_details[generated[0]]["target_repo"] == "github.com/example/repo"


@pytest.mark.parametrize("target_commit", [_FIXED_HEAD_COMMIT, "a" * 64])
def test_flat_add_operation_records_matching_target_commit(
    target_commit: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fallbackと同じtarget_repoの全メッセージへ投入時の完全OIDを記録する。"""
    notes = tmp_path / "private-notes"
    (notes / "inbox").mkdir(parents=True)
    monkeypatch.setattr(add_module, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(add_module, "_pull", lambda _path: None)
    monkeypatch.setattr(add_module, "_commit_and_push", lambda *_args, **_kwargs: None)

    generated = add_module.add_entries(
        notes,
        messages=["本文1", "---\ntarget_repo: github.com/example/repo\n---\n\n本文2"],
        target_repo="github.com/example/repo",
        target_commit=target_commit,
        source=None,
        now=_FIXED_DT,
    )

    for filename in generated:
        content = (notes / "inbox" / filename).read_text(encoding="utf-8")
        assert f"target_commit: {target_commit}" in content


def test_flat_add_operation_omits_commit_for_frontmatter_repo_override(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """frontmatterが別リポジトリを指定したメッセージへfallback側OIDを記録しない。"""
    notes = tmp_path / "private-notes"
    (notes / "inbox").mkdir(parents=True)
    monkeypatch.setattr(add_module, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(add_module, "_pull", lambda _path: None)
    monkeypatch.setattr(add_module, "_commit_and_push", lambda *_args, **_kwargs: None)
    message = "---\ntarget_repo: github.com/other/repo\n---\n\n本文"

    generated = add_module.add_entries(
        notes,
        messages=[message],
        target_repo="github.com/example/repo",
        target_commit=_FIXED_HEAD_COMMIT,
        source=None,
        now=_FIXED_DT,
    )

    content = (notes / "inbox" / generated[0]).read_text(encoding="utf-8")
    assert "target_repo: github.com/other/repo" in content
    assert "target_commit:" not in content


def test_flat_add_operation_drops_input_target_commit(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """利用者入力のtarget_commitを保存せず、システム確定値だけを採用する。"""
    notes = tmp_path / "private-notes"
    (notes / "inbox").mkdir(parents=True)
    monkeypatch.setattr(add_module, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(add_module, "_pull", lambda _path: None)
    monkeypatch.setattr(add_module, "_commit_and_push", lambda *_args, **_kwargs: None)
    message = f"---\ntarget_commit: {'f' * 40}\n---\n\n本文"

    generated = add_module.add_entries(
        notes,
        messages=[message],
        target_repo="github.com/example/repo",
        target_commit=_FIXED_HEAD_COMMIT,
        source=None,
        now=_FIXED_DT,
    )

    content = (notes / "inbox" / generated[0]).read_text(encoding="utf-8")
    assert f"target_commit: {_FIXED_HEAD_COMMIT}" in content
    assert f"target_commit: {'f' * 40}" not in content


@pytest.mark.parametrize("invalid_commit", ["abc123", "g" * 40, "a" * 41])
def test_flat_add_operation_rejects_non_full_oid(
    invalid_commit: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """共有add操作は40桁または64桁の16進完全OID以外を拒否する。"""
    notes = tmp_path / "private-notes"
    monkeypatch.setattr(add_module, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())

    with pytest.raises(WebInputError, match="完全OID"):
        add_module.add_entries(
            notes,
            messages=["本文"],
            target_repo="github.com/example/repo",
            target_commit=invalid_commit,
            source=None,
            now=_FIXED_DT,
        )


def test_flat_add_operation_normalizes_frontmatter_target_repo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """frontmatter由来target_repoが正規化されて保存される。"""
    notes = tmp_path / "private-notes"
    (notes / "inbox").mkdir(parents=True)
    monkeypatch.setattr(add_module, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(add_module, "_pull", lambda _path: None)
    monkeypatch.setattr(add_module, "_commit_and_push", lambda *_args, **_kwargs: None)
    # frontmatterに大文字小文字混在のURLを指定
    message = "---\ntarget_repo: GitHub.com/Example/Repo\nsource: test\n---\n\n本文\n"
    generated = add_module.add_entries(
        notes,
        messages=[message],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
    )
    content = (notes / "inbox" / generated[0]).read_text(encoding="utf-8")
    # 保存されたcontentは正規化後の値を含むべき
    assert "target_repo: github.com/example/repo" in content
    # 正規化前の値は含まれていない
    assert "GitHub.com/Example/Repo" not in content


def test_flat_add_operation_carries_over_unknown_frontmatter_keys(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """target_repo・source以外のfrontmatterキー（alert_keys等）を入力順で引き継ぐ。"""
    notes = tmp_path / "private-notes"
    (notes / "inbox").mkdir(parents=True)
    monkeypatch.setattr(add_module, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(add_module, "_pull", lambda _path: None)
    monkeypatch.setattr(add_module, "_commit_and_push", lambda *_args, **_kwargs: None)
    # 既に正規化済みのURLとサフィックス付きURLの両方をテスト
    message = "---\ntarget_repo: github.com/example/repo.git\nsource: alert-monitor\nalert_keys: github-run:1\n---\n\n本文\n"
    generated = add_module.add_entries(
        notes,
        messages=[message],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
    )
    content = (notes / "inbox" / generated[0]).read_text(encoding="utf-8")
    # alert_keysが引き継がれている
    assert "alert_keys: github-run:1" in content
    # target_repoが正規化されている（`.git`サフィックスが除去されている）
    assert "target_repo: github.com/example/repo" in content
    assert "github.com/example/repo.git" not in content
    assert content.index("source: alert-monitor") < content.index("alert_keys: github-run:1")


def test_flat_add_operation_drops_input_queue_schedule(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """利用者入力のqueue_scheduleを保存内容へ引き継がない。"""
    notes = tmp_path / "private-notes"
    (notes / "inbox").mkdir(parents=True)
    monkeypatch.setattr(add_module, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(add_module, "_pull", lambda _path: None)
    monkeypatch.setattr(add_module, "_commit_and_push", lambda *_args, **_kwargs: None)
    message = (
        "---\ntarget_repo: github.com/example/repo\nqueue_schedule:\n  type: normal\nalert_keys: github-run:1\n---\n\n本文\n"
    )

    generated = add_module.add_entries(
        notes,
        messages=[message],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
    )

    parsed = frontmatter.parse_frontmatter((notes / "inbox" / generated[0]).read_text(encoding="utf-8"))
    assert parsed is not None
    assert "queue_schedule" not in parsed[0]
    assert parsed[0]["alert_keys"] == "github-run:1"


@pytest.mark.parametrize(
    ("reserved_key", "reserved_value"),
    [
        ("repair_target", "broken.md"),
        ("repair_kind", "frontmatter"),
        ("cooldown_until", "2026-08-15T00:00:00+00:00"),
        ("reservation", "forged"),
        ("reservation_companion", "forged"),
        ("target_commit_history", "forged"),
    ],
)
def test_flat_add_operation_drops_input_repair_metadata(
    reserved_key: str,
    reserved_value: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """利用者入力の修復TBD予約キーを保存内容へ引き継がない。"""
    notes = tmp_path / "private-notes"
    (notes / "inbox").mkdir(parents=True)
    monkeypatch.setattr(add_module, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(add_module, "_pull", lambda _path: None)
    monkeypatch.setattr(add_module, "_commit_and_push", lambda *_args, **_kwargs: None)
    message = (
        f"---\ntarget_repo: github.com/example/repo\n{reserved_key}: {reserved_value}\nalert_keys: github-run:1\n---\n\n本文\n"
    )

    generated = add_module.add_entries(
        notes,
        messages=[message],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
    )

    parsed = frontmatter.parse_frontmatter((notes / "inbox" / generated[0]).read_text(encoding="utf-8"))
    assert parsed is not None
    assert reserved_key not in parsed[0]
    assert parsed[0]["alert_keys"] == "github-run:1"


def test_add_operation_classifies_explicit_plan_file(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """plan_file指定時に計画実装型の独立キーを記録する。"""
    notes = tmp_path / "private-notes"
    (notes / "inbox").mkdir(parents=True)
    plan = tmp_path / "plan.md"
    plan.write_text(
        "## 概要\n\n成果。\n\n### 計画メタ情報\n\n"
        "## 実装資料\n\n### 変更説明\n\nREADMEを更新する。\n\n"
        "## 完了条件\n\n検証。\n\n## 進捗ログ\n\n未着手。\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(add_module, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(add_module, "_pull", lambda _path: None)
    monkeypatch.setattr(add_module, "_commit_and_push", lambda *_args, **_kwargs: None)

    generated = add_module.add_entries(
        notes,
        messages=[f"対象計画ファイル: `{plan}`"],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
        plan_file=str(plan),
    )

    content = (notes / "inbox" / generated[0]).read_text(encoding="utf-8")
    parsed = frontmatter.parse_frontmatter(content)
    assert parsed is not None
    assert parsed[0]["plan_file"] == str(plan)
    assert "queue_schedule" not in parsed[0]


def test_add_operation_does_not_infer_plan_file_from_body(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本文が実在する計画ファイルへ言及しても未指定時は分類しない。"""
    notes = tmp_path / "private-notes"
    (notes / "inbox").mkdir(parents=True)
    plan = tmp_path / "plan.md"
    plan.write_text("# plan\n", encoding="utf-8")
    monkeypatch.setattr(add_module, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(add_module, "_pull", lambda _path: None)
    monkeypatch.setattr(add_module, "_commit_and_push", lambda *_args, **_kwargs: None)

    generated = add_module.add_entries(
        notes,
        messages=[f"対象計画ファイル: `{plan}`"],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
    )

    content = (notes / "inbox" / generated[0]).read_text(encoding="utf-8")
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
    (notes / "inbox").mkdir(parents=True)
    monkeypatch.setattr(add_module, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(add_module, "_pull", lambda _path: None)
    monkeypatch.setattr(add_module, "_commit_and_push", lambda *_args, **_kwargs: None)

    generated = add_module.add_entries(
        notes,
        messages=["本文"],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
        depends_on=("first.md", "second.md"),
    )

    parsed = frontmatter.parse_frontmatter((notes / "inbox" / generated[0]).read_text(encoding="utf-8"))
    assert parsed is not None
    assert parsed[0]["depends_on"] == ["first.md", "second.md"]


def test_add_cli_dependencies_are_validated_and_normalized(tmp_path: pathlib.Path) -> None:
    """`add` CLIの依存を単純ファイル名へ限定し、拡張子省略と重複を正規化する。"""
    inbox = tmp_path / "inbox"
    inbox.mkdir()

    assert add_module._normalize_dependencies(["first", "first.md", "second.md"], inbox) == (  # pylint: disable=protected-access  # noqa: SLF001
        "first.md",
        "second.md",
    )
    with pytest.raises(SystemExit) as exc_info:
        add_module._normalize_dependencies(["../outside"], inbox)  # pylint: disable=protected-access  # noqa: SLF001
    assert exc_info.value.code == 2


def test_add_rejects_dependencies_for_tbd(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """TBDで保存されないdepends_on指定を成功扱いにしない。"""
    _setup_notes(tmp_path)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(
            [
                "mq",
                "add",
                "--target-repo",
                "github.com/example/repo",
                "--type=tbd",
                "--depends-on",
                "feedback.md",
                "確認しますか？",
            ],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert exc_info.value.code == 1
    assert "--type=feedbackでのみ" in capsys.readouterr().err


@pytest.mark.parametrize("plan_file", ["relative-plan.md", "/missing-plan.md"])
def test_add_operation_rejects_invalid_plan_file(
    plan_file: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """plan_fileが相対パスまたは未実在の場合は投入を拒否する。"""
    notes = tmp_path / "private-notes"
    (notes / "inbox").mkdir(parents=True)
    monkeypatch.setattr(add_module, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(add_module, "_pull", lambda _path: None)
    monkeypatch.setattr(add_module, "_commit_and_push", lambda *_args, **_kwargs: None)

    with pytest.raises(WebInputError):
        add_module.add_entries(
            notes,
            messages=["本文"],
            target_repo="github.com/example/repo",
            source=None,
            now=_FIXED_DT,
            plan_file=plan_file,
        )


def test_add_operation_rejects_plan_file_for_tbd(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TBD種別とplan_fileの併用を拒否する。"""
    notes = tmp_path / "private-notes"
    (notes / "inbox").mkdir(parents=True)
    plan = tmp_path / "plan.md"
    plan.write_text("# plan\n", encoding="utf-8")
    monkeypatch.setattr(add_module, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(add_module, "_pull", lambda _path: None)
    monkeypatch.setattr(add_module, "_commit_and_push", lambda *_args, **_kwargs: None)

    with pytest.raises(WebInputError):
        add_module.add_entries(
            notes,
            messages=[f"対象計画ファイル: `{plan}`"],
            target_repo="github.com/example/repo",
            source=None,
            now=_FIXED_DT,
            entry_type=MQ_TYPE_TBD,
            question_type="free-form",
            plan_file=str(plan),
        )


def _write_plan_with_base_commit(tmp_path: pathlib.Path, value: str | None) -> pathlib.Path:
    """計画メタ情報を持つ計画ファイルを作成する。"""
    plan = tmp_path / "plan.md"
    base_line = "" if value is None else f"- ベースコミット: `{value}`\n"
    plan.write_text(f"## 実装契約\n\n### 計画メタ情報\n\n{base_line}", encoding="utf-8")
    return plan


def test_add_operation_rejects_plan_file_with_mismatched_base_commit(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = _prepare_notes(tmp_path, monkeypatch)
    plan = _write_plan_with_base_commit(tmp_path, "a" * 40)

    with pytest.raises(WebInputError, match="ベースコミット"):
        add_module.add_entries(
            notes,
            messages=["本文"],
            target_repo="github.com/example/repo",
            source=None,
            now=_FIXED_DT,
            target_commit="b" * 40,
            plan_file=str(plan),
        )

    assert not list((notes / "inbox").iterdir())


def test_add_operation_accepts_plan_file_with_matching_base_commit(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = _prepare_notes(tmp_path, monkeypatch)
    plan = _write_plan_with_base_commit(tmp_path, "a" * 40)

    generated = add_module.add_entries(
        notes,
        messages=["本文"],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
        target_commit="a" * 40,
        plan_file=str(plan),
    )

    assert len(generated) == 1


def test_add_operation_rejects_annotated_base_commit_mismatch(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """閉じバッククォート以降に注記がある既存記法でもHEAD照合を省略しない。"""
    notes = _prepare_notes(tmp_path, monkeypatch)
    plan = tmp_path / "plan.md"
    plan.write_text(
        f"## 実装契約\n\n### 計画メタ情報\n\n- ベースコミット: `{'a' * 40}`（`git rev-parse HEAD`で実測）\n",
        encoding="utf-8",
    )

    with pytest.raises(WebInputError, match="ベースコミット"):
        add_module.add_entries(
            notes,
            messages=["本文"],
            target_repo="github.com/example/repo",
            source=None,
            now=_FIXED_DT,
            target_commit="b" * 40,
            plan_file=str(plan),
        )

    assert not list((notes / "inbox").iterdir())


def test_add_operation_rejects_spoofed_or_duplicate_plan_metadata(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = _prepare_notes(tmp_path, monkeypatch)
    plan = tmp_path / "plan.md"
    plan.write_text(
        f"# 計画\n\n## 引用\n\n> ### 計画メタ情報\n>\n> - ベースコミット: `{'a' * 40}`\n\n"
        f"## 実装契約\n\n### 計画メタ情報\n\n- ベースコミット: `{'b' * 40}`\n\n"
        f"### 計画メタ情報\n\n- ベースコミット: `{'a' * 40}`\n",
        encoding="utf-8",
    )

    with pytest.raises(WebInputError, match="計画メタ情報"):
        add_module.add_entries(
            notes,
            messages=["本文"],
            target_repo="github.com/example/repo",
            source=None,
            now=_FIXED_DT,
            target_commit="a" * 40,
            plan_file=str(plan),
        )

    assert not list((notes / "inbox").iterdir())


def test_add_operation_ignores_blockquoted_plan_metadata(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = _prepare_notes(tmp_path, monkeypatch)
    plan = tmp_path / "plan.md"
    plan.write_text(
        f"# 計画\n\n## 引用\n\n> ### 計画メタ情報\n>\n> - ベースコミット: `{'a' * 40}`\n\n"
        f"## 実装契約\n\n### 計画メタ情報\n\n- ベースコミット: `{'b' * 40}`\n",
        encoding="utf-8",
    )

    with pytest.raises(WebInputError, match="ベースコミット"):
        add_module.add_entries(
            notes,
            messages=["本文"],
            target_repo="github.com/example/repo",
            source=None,
            now=_FIXED_DT,
            target_commit="a" * 40,
            plan_file=str(plan),
        )

    assert not list((notes / "inbox").iterdir())


def test_add_operation_warns_when_plan_file_lacks_base_commit(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    notes = _prepare_notes(tmp_path, monkeypatch)
    plan = _write_plan_with_base_commit(tmp_path, None)

    generated = add_module.add_entries(
        notes,
        messages=["本文"],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
        target_commit="a" * 40,
        plan_file=str(plan),
    )

    assert len(generated) == 1
    assert "完全OIDを抽出できない" in capsys.readouterr().err


def test_add_operation_accepts_canonical_plan_metadata(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """新しい正規配置の`## 概要`直下からベースコミットを照合する。"""
    notes = _prepare_notes(tmp_path, monkeypatch)
    plan = tmp_path / "canonical-plan.md"
    plan.write_text(
        "# 計画\n\n## 概要\n\n### 計画メタ情報\n\n"
        "- 起動経路: `agent-toolkit:plan-mode`\n"
        "- 対象リポジトリ: `/repo`\n"
        "- 作業種別: 通常変更\n"
        f"- ベースコミット: `{'a' * 40}`\n",
        encoding="utf-8",
    )

    generated = add_module.add_entries(
        notes,
        messages=["本文"],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
        target_commit="a" * 40,
        plan_file=str(plan),
    )

    assert len(generated) == 1


def test_add_operation_prefers_canonical_over_legacy_plan_metadata(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正規配置と旧配置が併存する移行期の計画では正規配置の値を採用する。"""
    notes = _prepare_notes(tmp_path, monkeypatch)
    plan = tmp_path / "transitional-plan.md"
    plan.write_text(
        f"# 計画\n\n## 概要\n\n### 計画メタ情報\n\n- ベースコミット: `{'a' * 40}`\n\n"
        f"## 実装契約\n\n### 計画メタ情報\n\n- ベースコミット: `{'b' * 40}`\n",
        encoding="utf-8",
    )

    generated = add_module.add_entries(
        notes,
        messages=["本文"],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
        target_commit="a" * 40,
        plan_file=str(plan),
    )

    assert len(generated) == 1


def test_add_operation_rejects_metadata_split_across_legacy_sections(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧配置の候補が複数のH2へ分かれる計画は曖昧として拒否する。"""
    notes = _prepare_notes(tmp_path, monkeypatch)
    plan = tmp_path / "ambiguous-plan.md"
    plan.write_text(
        f"# 計画\n\n## 背景\n\n### 計画メタ情報\n\n- ベースコミット: `{'a' * 40}`\n\n"
        f"## 実装契約\n\n### 計画メタ情報\n\n- ベースコミット: `{'b' * 40}`\n",
        encoding="utf-8",
    )

    with pytest.raises(WebInputError, match="計画メタ情報"):
        add_module.add_entries(
            notes,
            messages=["本文"],
            target_repo="github.com/example/repo",
            source=None,
            now=_FIXED_DT,
            target_commit="a" * 40,
            plan_file=str(plan),
        )

    assert not list((notes / "inbox").iterdir())


def test_add_operation_warns_when_base_commit_lacks_backticks(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """囲み違反のベースコミットは候補として採らずHEAD照合を省略する。"""
    notes = _prepare_notes(tmp_path, monkeypatch)
    plan = tmp_path / "unquoted-plan.md"
    plan.write_text(
        f"# 計画\n\n## 概要\n\n### 計画メタ情報\n\n- ベースコミット: {'a' * 40}\n",
        encoding="utf-8",
    )

    generated = add_module.add_entries(
        notes,
        messages=["本文"],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
        target_commit="b" * 40,
        plan_file=str(plan),
    )

    assert len(generated) == 1
    assert "完全OIDを抽出できない" in capsys.readouterr().err


def test_add_operation_accepts_legacy_plan_metadata(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """現行形式が無い既存計画では背景直下のメタ情報を照合する。"""
    notes = _prepare_notes(tmp_path, monkeypatch)
    plan = tmp_path / "legacy-plan.md"
    plan.write_text(
        f"## 背景\n\n### 計画メタ情報\n\n- ベースコミット: `{'a' * 40}`\n",
        encoding="utf-8",
    )

    generated = add_module.add_entries(
        notes,
        messages=["本文"],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
        target_commit="a" * 40,
        plan_file=str(plan),
    )

    assert len(generated) == 1


def test_add_operation_warns_when_legacy_plan_lacks_metadata(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    notes = _prepare_notes(tmp_path, monkeypatch)
    plan = tmp_path / "legacy-plan.md"
    plan.write_text("# 旧計画\n", encoding="utf-8")

    generated = add_module.add_entries(
        notes,
        messages=["本文"],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
        target_commit="a" * 40,
        plan_file=str(plan),
    )

    assert len(generated) == 1
    assert "完全OIDを抽出できない" in capsys.readouterr().err


def test_add_operation_ignores_base_commit_inside_metadata_code_fence(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = _prepare_notes(tmp_path, monkeypatch)
    plan = tmp_path / "plan.md"
    plan.write_text(
        f"## 実装契約\n\n### 計画メタ情報\n\n```text\n- ベースコミット: `{'a' * 40}`\n```\n\n- ベースコミット: `{'b' * 40}`\n",
        encoding="utf-8",
    )

    with pytest.raises(WebInputError, match="ベースコミット"):
        add_module.add_entries(
            notes,
            messages=["本文"],
            target_repo="github.com/example/repo",
            source=None,
            now=_FIXED_DT,
            target_commit="a" * 40,
            plan_file=str(plan),
        )

    assert not list((notes / "inbox").iterdir())


def test_add_operation_rejects_duplicate_base_commit_candidates(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = _prepare_notes(tmp_path, monkeypatch)
    plan = tmp_path / "plan.md"
    plan.write_text(
        f"## 実装契約\n\n### 計画メタ情報\n\n- ベースコミット: `{'a' * 40}`\n- 基準コミット: `{'a' * 40}`\n",
        encoding="utf-8",
    )

    with pytest.raises(WebInputError, match="複数"):
        add_module.add_entries(
            notes,
            messages=["本文"],
            target_repo="github.com/example/repo",
            source=None,
            now=_FIXED_DT,
            target_commit="a" * 40,
            plan_file=str(plan),
        )

    assert not list((notes / "inbox").iterdir())


def test_add_operation_warns_when_plan_file_base_commit_is_abbreviated(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    notes = _prepare_notes(tmp_path, monkeypatch)
    plan = _write_plan_with_base_commit(tmp_path, "01234567")

    generated = add_module.add_entries(
        notes,
        messages=["本文"],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
        target_commit="a" * 40,
        plan_file=str(plan),
    )

    assert len(generated) == 1
    assert "完全OIDを抽出できない" in capsys.readouterr().err


def test_add_operation_rejects_plan_file_with_target_repo_override(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = _prepare_notes(tmp_path, monkeypatch)
    plan = _write_plan_with_base_commit(tmp_path, "a" * 40)
    message = "---\ntarget_repo: github.com/other/repo\n---\n\n本文"

    with pytest.raises(WebInputError, match="対象リポジトリ"):
        add_module.add_entries(
            notes,
            messages=[message],
            target_repo="github.com/example/repo",
            source=None,
            now=_FIXED_DT,
            target_commit="a" * 40,
            plan_file=str(plan),
        )

    assert not list((notes / "inbox").iterdir())


def test_add_operation_accepts_plan_file_with_equivalent_target_repo(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = _prepare_notes(tmp_path, monkeypatch)
    plan = _write_plan_with_base_commit(tmp_path, "a" * 40)
    message = "---\ntarget_repo: GitHub.com/Example/Repo.git\n---\n\n本文"

    generated = add_module.add_entries(
        notes,
        messages=[message],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
        target_commit="a" * 40,
        plan_file=str(plan),
    )

    assert len(generated) == 1


def test_add_operation_rejects_all_messages_when_one_overrides_target_repo(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = _prepare_notes(tmp_path, monkeypatch)
    plan = _write_plan_with_base_commit(tmp_path, "a" * 40)
    messages = ["本文1", "---\ntarget_repo: github.com/other/repo\n---\n\n本文2"]

    with pytest.raises(WebInputError, match="対象リポジトリ"):
        add_module.add_entries(
            notes,
            messages=messages,
            target_repo="github.com/example/repo",
            source=None,
            now=_FIXED_DT,
            target_commit="a" * 40,
            plan_file=str(plan),
        )

    assert not list((notes / "inbox").iterdir())


class TestAddOrderEditorFirst:
    """addサブコマンド: エディター起動を`_pull`より前に呼ぶ順序保証。"""

    def test_editor_invoked_before_pull(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """messages省略時、エディターは`_pull`より前に起動される（対象リポジトリはcwdから解決）。"""
        notes = _setup_notes(tmp_path)
        monkeypatch.setenv("EDITOR", "fake-editor")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        call_order: list[str] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "-C", str(myrepo), "rev-parse", "--verify", "HEAD^{commit}"]:
                call_order.append("head")
            resp = _fake_git_worktree_remote_response(cmd, myrepo, kwargs)
            if resp is not None:
                return resp
            empty: Any = "" if kwargs.get("text") else b""
            if cmd[0] == "fake-editor":
                call_order.append("editor")
                pathlib.Path(cmd[1]).write_text("本文テスト", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)
            if cmd[:2] == ["git", "fetch"]:
                call_order.append("sync")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        assert call_order == ["editor", "head", "sync"]
        assert list((notes / "inbox").iterdir())

    def test_message_preserved_when_remote_sync_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """remote同期失敗時、エディターで確定済みの本文がstderrへ再表示されたうえで終了コード1になる。"""
        _setup_notes(tmp_path)
        monkeypatch.setenv("EDITOR", "fake-editor")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            resp = _fake_git_worktree_remote_response(cmd, myrepo, kwargs)
            if resp is not None:
                return resp
            empty: Any = "" if kwargs.get("text") else b""
            if cmd[0] == "fake-editor":
                pathlib.Path(cmd[1]).write_text("消失させたくない本文", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)
            if cmd[:2] == ["git", "merge"]:
                raise subprocess.CalledProcessError(returncode=1, cmd=cmd)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "remote同期に失敗" in captured.err
        assert "消失させたくない本文" in captured.err

    def test_explicit_message_still_pulls_before_write(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """引数指定経路でもremote同期→書き込み→commitの順序で動作すること。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        git_cmds: list[list[str]] = []
        inbox = notes / "inbox"

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            resp = _fake_git_worktree_remote_response(cmd, myrepo, kwargs)
            if resp is not None:
                return resp
            empty: Any = "" if kwargs.get("text") else b""
            if cmd[0] == "git":
                git_cmds.append(list(cmd))
                if cmd[:2] == ["git", "add"]:
                    # add時点でinboxにファイルが存在することを確認する
                    assert list(inbox.iterdir()), "書き込みはgit add前に完了している必要がある"
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", str(myrepo), "本文"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        assert git_cmds[:2] == [["git", "fetch"], ["git", "merge", "--ff-only", "@{u}"]]


class TestAddRepoPathOverrideCli:
    """`mq add`のREPO_PATH位置引数廃止に伴うCLI事前変換層の検証。"""

    def test_repo_path_omitted_resolves_from_cwd(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """REPO_PATH省略時、対象リポジトリはカレントディレクトリのgit worktreeから解決される。"""
        notes = _setup_notes(tmp_path)
        cwd_repo = tmp_path / "cwdrepo"
        cwd_repo.mkdir()

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            resp = _fake_git_worktree_remote_response(cmd, cwd_repo, kwargs)
            if resp is not None:
                return resp
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", "本文"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        content = next((notes / "inbox").iterdir()).read_text(encoding="utf-8")
        # _fake_git_worktree_remote_responseは固定URL（example/myrepo.git）を返すため、
        # 実際のディレクトリ名（cwdrepo）に関わらずtarget_repoはmyrepoで確定する
        assert "target_repo: github.com/example/myrepo" in content
        assert f"target_commit: {_FIXED_HEAD_COMMIT}" in content

    def test_message_only_directory_errors(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """本文が続かないディレクトリのみの呼び出しは、usage表示付きの平易なエラーでexit 2になる。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", str(myrepo)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "usage: atk mq add" in captured.err
        error_line = captured.err.rstrip("\n").splitlines()[-1]
        assert "パスの指定は不要です" in error_line
        assert "REPO_PATH" not in error_line
        assert "MESSAGE" not in error_line

    def test_directory_followed_by_message_uses_compat_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """MESSAGE先頭が実在ディレクトリで残り本文がある場合、旧REPO_PATH形式として互換動作する。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            resp = _fake_git_worktree_remote_response(cmd, myrepo, kwargs)
            if resp is not None:
                return resp
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", str(myrepo), "本文"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        content = next((notes / "inbox").iterdir()).read_text(encoding="utf-8")
        assert "target_repo: github.com/example/myrepo" in content
        assert f"target_commit: {_FIXED_HEAD_COMMIT}" in content
        assert "本文" in content

    def test_directory_followed_by_option_and_message_uses_compat_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """REPO_PATHとMESSAGEの間にオプションを配置する旧形式でも互換動作する。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            resp = _fake_git_worktree_remote_response(cmd, myrepo, kwargs)
            if resp is not None:
                return resp
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "add", str(myrepo), "--source", "session-review", "本文"],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 0
        content = next((notes / "inbox").iterdir()).read_text(encoding="utf-8")
        assert "target_repo: github.com/example/myrepo" in content
        assert "本文" in content
        assert "source: session-review" in content

    def test_oversized_message_does_not_raise_oserror(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """OS上限を超える長さのMESSAGEでもREPO_PATH誤検出による`OSError`を送出せずcwd解決される。"""
        notes = _setup_notes(tmp_path)
        cwd_repo = tmp_path / "cwdrepo"
        cwd_repo.mkdir()
        oversized_message = "本文" * 5000

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            resp = _fake_git_worktree_remote_response(cmd, cwd_repo, kwargs)
            if resp is not None:
                return resp
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", oversized_message], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        content = next((notes / "inbox").iterdir()).read_text(encoding="utf-8")
        # _fake_git_worktree_remote_responseは固定URL（example/myrepo.git）を返すため、
        # 実際のディレクトリ名（cwdrepo）に関わらずtarget_repoはmyrepoで確定する
        assert "target_repo: github.com/example/myrepo" in content
        assert oversized_message in content


def test_cli_add_omits_target_commit_for_url_only_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """URLだけのtarget_repo指定ではローカルworktreeを推測せずOIDを省略する。"""
    notes = _setup_notes(tmp_path)
    git_commands: list[list[str]] = []

    def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        if cmd[0] == "git":
            git_commands.append(list(cmd))
        empty: Any = "" if kwargs.get("text") else b""
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(
            ["mq", "add", "--target-repo", "github.com/example/remote", "本文"],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert exc_info.value.code == 0
    content = next((notes / "inbox").iterdir()).read_text(encoding="utf-8")
    assert "target_repo: github.com/example/remote" in content
    assert "target_commit:" not in content
    assert not any("HEAD^{commit}" in command for command in git_commands)


def test_cli_add_rejects_existing_path_outside_worktree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """実在してもworktreeではないパスをローカル投入先として拒否する。"""
    notes = _setup_notes(tmp_path)
    bare_repo = tmp_path / "bare.git"
    bare_repo.mkdir()
    git_commands: list[list[str]] = []

    def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        git_commands.append(list(cmd))
        empty: Any = "" if kwargs.get("text") else b""
        if cmd == ["git", "-C", str(bare_repo), "rev-parse", "--is-inside-work-tree"]:
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="false\n", stderr="")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["mq", "add", str(bare_repo), "本文"], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 2
    assert "ローカルworktreeではありません" in capsys.readouterr().err
    assert not list((notes / "inbox").iterdir())
    assert not any(command[-3:] == ["rev-parse", "--verify", "HEAD^{commit}"] for command in git_commands)


@pytest.mark.parametrize(("returncode", "stdout"), [(1, ""), (0, "short")])
def test_cli_add_rejects_unresolved_head_commit(
    returncode: int,
    stdout: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ローカルworktreeのHEAD取得失敗または不正OIDをexit 2で拒否する。"""
    notes = _setup_notes(tmp_path)
    myrepo = tmp_path / "myrepo"
    myrepo.mkdir()

    def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        empty: Any = "" if kwargs.get("text") else b""
        if cmd == ["git", "-C", str(myrepo), "rev-parse", "--is-inside-work-tree"]:
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="true\n", stderr="")
        if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(
                cmd,
                returncode=0,
                stdout="https://github.com/example/myrepo.git\n",
                stderr="",
            )
        if cmd == ["git", "-C", str(myrepo), "rev-parse", "--verify", "HEAD^{commit}"]:
            return subprocess.CompletedProcess(cmd, returncode=returncode, stdout=stdout, stderr="取得失敗")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["mq", "add", str(myrepo), "本文"], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 2
    assert not list((notes / "inbox").iterdir())
    assert "HEADコミット" in capsys.readouterr().err


def test_editor_body_is_preserved_when_head_resolution_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """エディター確定後のHEAD取得失敗時に本文を標準エラーへ再表示する。"""
    notes = _setup_notes(tmp_path)
    monkeypatch.setenv("EDITOR", "fake-editor")
    myrepo = tmp_path / "myrepo"
    myrepo.mkdir()

    def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        empty: Any = "" if kwargs.get("text") else b""
        if cmd == ["git", "rev-parse", "--show-toplevel"]:
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=f"{myrepo}\n", stderr="")
        if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
            return subprocess.CompletedProcess(
                cmd,
                returncode=0,
                stdout="https://github.com/example/myrepo.git\n",
                stderr="",
            )
        if cmd[0] == "fake-editor":
            pathlib.Path(cmd[1]).write_text("失いたくない本文", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)
        if cmd == ["git", "-C", str(myrepo), "rev-parse", "--verify", "HEAD^{commit}"]:
            return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="HEADなし")
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["mq", "add"], home=tmp_path, now=_FIXED_DT)

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "確定済みの本文" in captured.err
    assert "失いたくない本文" in captured.err
    assert not list((notes / "inbox").iterdir())


class TestAddEmptyBodyRejection:
    """`mq add`の実質空本文投入拒否を検証する。"""

    def test_empty_string_body_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """本文が空文字のみのとき非ゼロ終了し、エラー出力に検出理由が含まれる。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            resp = _fake_git_worktree_remote_response(cmd, myrepo, kwargs)
            if resp is not None:
                return resp
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", str(myrepo), ""], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "実質空" in captured.err

    def test_single_dash_body_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """本文が単一の`-`のみのとき非ゼロ終了する。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            resp = _fake_git_worktree_remote_response(cmd, myrepo, kwargs)
            if resp is not None:
                return resp
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", str(myrepo), "-"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1

    def test_multiline_bullet_markers_only_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数行の箇条書きマーカーのみ（`-\\n-\\n`等）のとき非ゼロ終了する。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            resp = _fake_git_worktree_remote_response(cmd, myrepo, kwargs)
            if resp is not None:
                return resp
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        # 先頭ハイフンの複数文字列引数はargparseがオプションと誤認するため`--`で区切る
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", str(myrepo), "--", "-\n-\n"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1

    def test_valid_body_with_content_still_accepted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """内容ありの箇条書き（`- 理由: ...`）は従来通り投入が成立する。"""
        notes = _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            resp = _fake_git_worktree_remote_response(cmd, myrepo, kwargs)
            if resp is not None:
                return resp
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", str(myrepo), "- 理由: 動作確認済みのため採用"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        assert list((notes / "inbox").iterdir())

    def test_editor_confirmed_empty_body_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """エディター経由で空本文が確定した場合も非ゼロ終了する。"""
        _setup_notes(tmp_path)
        monkeypatch.setenv("EDITOR", "fake-editor")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            resp = _fake_git_worktree_remote_response(cmd, myrepo, kwargs)
            if resp is not None:
                return resp
            empty: Any = "" if kwargs.get("text") else b""
            if cmd[0] == "fake-editor":
                pathlib.Path(cmd[1]).write_text("-\n", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1


def _prepare_notes(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """git操作を無効化したprivate-notesディレクトリを用意する。

    既存テスト（`test_flat_add_operation_is_public`等）と同じ差し替え方針に揃える。
    """
    notes = tmp_path / "private-notes"
    (notes / "inbox").mkdir(parents=True)
    monkeypatch.setattr(add_module, "_repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(add_module, "_pull", lambda _path: None)
    monkeypatch.setattr(add_module, "_commit_and_push", lambda *_args, **_kwargs: None)
    return notes


def test_add_entries_rejects_answer_marker_in_tbd_body(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TBD本文が回答欄マーカーを含む場合に投入を拒否する。"""
    notes = _prepare_notes(tmp_path, monkeypatch)
    with pytest.raises(WebInputError):
        add_module.add_entries(
            notes,
            messages=[f"この方針を採用しますか？\n\n{tbd_module.ANSWER_MARKER}\n"],
            target_repo="github.com/example/repo",
            source=None,
            now=_FIXED_DT,
            entry_type=MQ_TYPE_TBD,
            question_type="free-form",
        )


def test_add_entries_rejects_answer_heading_in_tbd_body(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TBD本文が回答見出しを行頭に含む場合に投入を拒否する。"""
    notes = _prepare_notes(tmp_path, monkeypatch)
    with pytest.raises(WebInputError):
        add_module.add_entries(
            notes,
            messages=[f"この方針を採用しますか？\n\n{tbd_module.ANSWER_HEADING}\n\n未記入\n"],
            target_repo="github.com/example/repo",
            source=None,
            now=_FIXED_DT,
            entry_type=MQ_TYPE_TBD,
            question_type="free-form",
        )


class TestAddBodyFile:
    """`mq add --body-file`によるシェル引用符を経由しない本文入力経路を検証する。"""

    @staticmethod
    def _fake_git(monkeypatch: pytest.MonkeyPatch, repo: pathlib.Path) -> None:
        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            resp = _fake_git_worktree_remote_response(cmd, repo, kwargs)
            if resp is not None:
                return resp
            empty: Any = "" if kwargs.get("text") else b""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=empty, stderr=empty)

        monkeypatch.setattr(subprocess, "run", fake_run)

    def test_body_file_content_becomes_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """指定ファイルの内容が本文として投入される。"""
        notes = _setup_notes(tmp_path)
        repo = tmp_path / "myrepo"
        repo.mkdir()
        self._fake_git(monkeypatch, repo)
        # シェルの引用規則を経由すると破損しやすい文字を含む本文を用いる。
        body = '### 見出し\n\n- `バッククォート` と $変数 と "引用符" を含む本文\n'
        body_path = tmp_path / "body.md"
        body_path.write_text(body, encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", "--body-file", str(body_path)], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 0
        content = next((notes / "inbox").iterdir()).read_text(encoding="utf-8")
        assert body.strip() in content

    def test_body_file_repeats_for_multiple_entries(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """複数回指定すると指定件数のエントリが投入される。"""
        notes = _setup_notes(tmp_path)
        repo = tmp_path / "myrepo"
        repo.mkdir()
        self._fake_git(monkeypatch, repo)
        first = tmp_path / "first.md"
        second = tmp_path / "second.md"
        first.write_text("1件目の本文\n", encoding="utf-8")
        second.write_text("2件目の本文\n", encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["mq", "add", "--body-file", str(first), "--body-file", str(second)],
                home=tmp_path,
                now=_FIXED_DT,
            )

        assert exc_info.value.code == 0
        bodies = sorted(path.read_text(encoding="utf-8") for path in (notes / "inbox").iterdir())
        assert len(bodies) == 2
        assert any("1件目の本文" in body for body in bodies)
        assert any("2件目の本文" in body for body in bodies)

    def test_body_file_rejects_positional_message(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """MESSAGE位置引数との併用はusage表示付きでexit 2になる。"""
        _setup_notes(tmp_path)
        body_path = tmp_path / "body.md"
        body_path.write_text("本文\n", encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", "--body-file", str(body_path), "本文"], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "併用できません" in captured.err

    def test_body_file_missing_path_rejected(
        self,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """読み込みに失敗するパスを指定した場合は投入せず非ゼロ終了する。"""
        _setup_notes(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["mq", "add", "--body-file", str(tmp_path / "missing.md")], home=tmp_path, now=_FIXED_DT)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "--body-file" in captured.err


def test_add_entries_accepts_plain_tbd_body(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """予約書式を含まないTBD本文は投入され、見出しと回答欄が1組だけ生成される。"""
    notes = _prepare_notes(tmp_path, monkeypatch)
    generated = add_module.add_entries(
        notes,
        messages=["どちらの案を採用しますか？判定根拠は次のとおり。"],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
        entry_type=MQ_TYPE_TBD,
        question_type="free-form",
    )
    content = (notes / "inbox" / generated[0]).read_text(encoding="utf-8")
    assert content.count(tbd_module.ANSWER_MARKER) == 1
    assert content.count(f"\n{tbd_module.ANSWER_HEADING}\n") == 1
    assert content.count(f"\n{tbd_module.QUESTION_HEADING}\n") == 1
