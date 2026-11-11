# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""WIのadd: metadataのテスト。"""

# pylint: disable=protected-access,subprocess-run-check

import contextlib
import pathlib
import subprocess
from collections.abc import Generator

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import add as add_module
from agent_toolkit._atk.wi import frontmatter
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi.constants import WI_TYPE_UWI
from agent_toolkit._atk.wi.web_input import WebInputError
from agent_toolkit._testing.git_fakes import _FIXED_HEAD_COMMIT
from agent_toolkit._testing.wi_add_support import _invoke_add_body_file, _prepare_notes
from agent_toolkit._testing.wi_add_support import _isolate_managed_temp as _isolate_managed_temp
from agent_toolkit._testing.wi_bodies import AGENT_AWI_BODY as _AGENT_AWI_BODY
from agent_toolkit._testing.wi_bodies import AGENT_AWI_SECTIONS as _AGENT_AWI_SECTIONS
from agent_toolkit.atk_test import _FIXED_DT, _setup_notes


def test_flat_add_operation_is_public(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """公開の`add_entries`が生成したファイル名を返し、frontmatter付きファイルを書き込む。"""
    notes = tmp_path / "private-notes"
    (notes / "2024" / "01").mkdir(parents=True)
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    generated = add_module.add_entries(
        notes,
        messages=[_AGENT_AWI_BODY],
        target_repo="github.com/example/repo",
        source="test",
        now=_FIXED_DT,
    )
    assert generated == [f"{_FIXED_DT:%Y%m%d-%H%M%S}-001.md"]
    content = (notes / "2024" / "01" / generated[0]).read_text(encoding="utf-8")
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
    def tracked_lock(*_args: object, **_kwargs: object) -> Generator[None]:
        lock_state["held"] = True
        try:
            yield
        finally:
            lock_state["held"] = False

    original_read = add_module.read_saved_entry_details  # pylint: disable=protected-access  # noqa: SLF001

    def read_while_locked(path: pathlib.Path, *, expected_body: str) -> dict[str, object | None]:
        assert lock_state["held"]
        return original_read(path, expected_body=expected_body)

    monkeypatch.setattr(_wi_sync, "repo_lock", tracked_lock)
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(add_module, "read_saved_entry_details", read_while_locked)
    saved_details: dict[str, dict[str, object | None]] = {}

    generated = add_module.add_entries(
        notes,
        messages=[_AGENT_AWI_BODY],
        target_repo="github.com/example/repo",
        source="test",
        now=_FIXED_DT,
        saved_details=saved_details,
    )

    assert saved_details[generated[0]]["target_repo"] == "github.com/example/repo"
    assert "saved_body" not in saved_details[generated[0]]
    assert "body_match" not in saved_details[generated[0]]


def test_cli_add_does_not_output_body_verification_details(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """複数件投入でも本文の一致を確認する際の内部情報は出力しない。"""
    _setup_notes(tmp_path)
    messages = [
        '1件目。"引用"を含む。\n\n## 見出し\n\n複数行。',
        "2件目。\n\n```text\n字下げしない本文\n```",
    ]
    body_paths: list[str] = []
    for index, message in enumerate(messages):
        body_path = tmp_path / f"body-{index}.md"
        body_path.write_text(message, encoding="utf-8")
        body_paths.extend(("--body-file", str(body_path)))
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, "", ""))

    with pytest.raises(SystemExit) as exc_info:
        atk.main(
            ["wi", "add", "--target-repo", "github.com/example/repo", *body_paths],
            home=tmp_path,
            now=_FIXED_DT,
        )

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "body_match" not in output
    assert "saved_body" not in output
    assert messages[0] not in output
    assert messages[1] not in output


@pytest.mark.parametrize("target_commit", [_FIXED_HEAD_COMMIT, "a" * 64])
def test_flat_add_operation_records_matching_target_commit(
    target_commit: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fallbackと同じtarget_repoの全メッセージへ投入時の40文字OIDを記録する。"""
    notes = tmp_path / "private-notes"
    (notes / "2024" / "01").mkdir(parents=True)
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)

    generated = add_module.add_entries(
        notes,
        messages=["本文1", "---\ntarget_repo: github.com/example/repo\n---\n\n本文2"],
        target_repo="github.com/example/repo",
        target_commit=target_commit,
        source=None,
        now=_FIXED_DT,
    )

    for filename in generated:
        content = (notes / "2024" / "01" / filename).read_text(encoding="utf-8")
        assert f"target_commit: {target_commit}" in content


def test_flat_add_operation_omits_commit_for_frontmatter_repo_override(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """frontmatterが別リポジトリを指定したメッセージへfallback側OIDを記録しない。"""
    notes = tmp_path / "private-notes"
    (notes / "2024" / "01").mkdir(parents=True)
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    message = "---\ntarget_repo: github.com/other/repo\n---\n\n本文"

    generated = add_module.add_entries(
        notes,
        messages=[message],
        target_repo="github.com/example/repo",
        target_commit=_FIXED_HEAD_COMMIT,
        source=None,
        now=_FIXED_DT,
    )

    content = (notes / "2024" / "01" / generated[0]).read_text(encoding="utf-8")
    assert "target_repo: github.com/other/repo" in content
    assert "target_commit:" not in content


def test_flat_add_operation_rejects_input_target_commit(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自動記録するtarget_commitの本文指定は、保存前に拒否する。"""
    notes = tmp_path / "private-notes"
    (notes / "2024" / "01").mkdir(parents=True)
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    message = f"---\ntarget_commit: {'f' * 40}\n---\n\n本文"

    with pytest.raises(WebInputError, match="target_commit"):
        add_module.add_entries(
            notes,
            messages=[message],
            target_repo="github.com/example/repo",
            target_commit=_FIXED_HEAD_COMMIT,
            source=None,
            now=_FIXED_DT,
        )
    assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))


@pytest.mark.parametrize("invalid_commit", ["abc123", "g" * 40, "a" * 41])
def test_flat_add_operation_rejects_non_full_oid(
    invalid_commit: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """共有add操作は解決済みの40桁または64桁OID以外を拒否する。"""
    notes = tmp_path / "private-notes"
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())

    with pytest.raises(WebInputError, match="解決済み"):
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
    (notes / "2024" / "01").mkdir(parents=True)
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    # frontmatterに大文字小文字混在のURLを指定
    message = f"---\ntarget_repo: GitHub.com/Example/Repo\nsource: test\n---\n\n{_AGENT_AWI_BODY}"
    generated = add_module.add_entries(
        notes,
        messages=[message],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
    )
    content = (notes / "2024" / "01" / generated[0]).read_text(encoding="utf-8")
    # 保存されたcontentは正規化後の値を含むべき
    assert "target_repo: github.com/example/repo" in content
    # 正規化前の値は含まれていない
    assert "GitHub.com/Example/Repo" not in content


def test_flat_add_operation_carries_over_unknown_frontmatter_keys(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """target_repo・source以外のfrontmatterキー（alert_keys等）を入力順で引き継ぐ。"""
    notes = tmp_path / "private-notes"
    (notes / "2024" / "01").mkdir(parents=True)
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    # 既に正規化済みのURLとサフィックス付きURLの両方をテスト
    message = (
        "---\ntarget_repo: github.com/example/repo.git\nsource: alert-monitor\n"
        f"alert_keys: github-run:1\n---\n\n{_AGENT_AWI_BODY}"
    )
    generated = add_module.add_entries(
        notes,
        messages=[message],
        target_repo="github.com/example/repo",
        source=None,
        now=_FIXED_DT,
    )
    content = (notes / "2024" / "01" / generated[0]).read_text(encoding="utf-8")
    # alert_keysが引き継がれている
    assert "alert_keys: github-run:1" in content
    # target_repoが正規化されている（`.git`サフィックスが除去されている）
    assert "target_repo: github.com/example/repo" in content
    assert "github.com/example/repo.git" not in content
    assert content.index("source: alert-monitor") < content.index("alert_keys: github-run:1")


def test_flat_add_operation_preserves_nonreserved_frontmatter_for_cross_repository_transfer(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """別リポジトリへ移管する本文が持つ非予約frontmatter（`source`・`alert_keys`）を保存時に保持する。"""
    notes = tmp_path / "private-notes"
    (notes / "2024" / "01").mkdir(parents=True)
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    message = (
        f"---\ntarget_repo: github.com/target/repo\nsource: alert-monitor\nalert_keys: github-run:1\n---\n\n{_AGENT_AWI_BODY}"
    )

    generated = add_module.add_entries(
        notes,
        messages=[message],
        target_repo="github.com/target/repo",
        source=None,
        now=_FIXED_DT,
    )

    parsed = frontmatter.parse_frontmatter((notes / "2024" / "01" / generated[0]).read_text(encoding="utf-8"))
    assert parsed is not None
    data, body = parsed
    assert data["target_repo"] == "github.com/target/repo"
    assert data["source"] == "alert-monitor"
    assert data["alert_keys"] == "github-run:1"
    assert body.strip() == _AGENT_AWI_BODY


def test_flat_add_operation_rejects_input_queue_schedule(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """通常投入でqueue_scheduleを指定すると、保存前にbatchの利用を案内する。"""
    notes = tmp_path / "private-notes"
    (notes / "2024" / "01").mkdir(parents=True)
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    message = (
        "---\ntarget_repo: github.com/example/repo\nqueue_schedule:\n  type: normal\nalert_keys: github-run:1\n---\n\n本文\n"
    )

    with pytest.raises(WebInputError, match="queue_schedule") as error:
        add_module.add_entries(
            notes,
            messages=[message],
            target_repo="github.com/example/repo",
            source=None,
            now=_FIXED_DT,
        )
    assert "--batch" in error.value.next_action
    assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))


@pytest.mark.parametrize(
    ("reserved_key", "reserved_value"),
    [
        ("type", "awi"),
        ("state", "processing"),
        ("scope", "agent"),
        ("question_type", "yes-no"),
        ("choices", "A,B"),
        ("depends_on", "[]"),
        ("repair_target", "broken.md"),
        ("repair_kind", "frontmatter"),
        ("cooldown_until", "2026-08-15T00:00:00+00:00"),
        ("reservation", "forged"),
        ("reservation_companion", "forged"),
        ("target_commit_history", "forged"),
        ("origin_session", "forged-session"),
        ("origin_locator", "forged-rollout:1"),
        ("submitter_session", "forged-session"),
        ("plan_file", "$(atk config get private_notes)/plans/2026/09/forged.md"),
    ],
)
@pytest.mark.parametrize("entry_type", ["awi", "uwi"])
def test_flat_add_operation_rejects_reserved_metadata_atomically(
    reserved_key: str,
    reserved_value: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    entry_type: str,
) -> None:
    """予約キーの本文指定は、複数本文のどの位置でも全件を保存前に拒否する。"""
    notes = tmp_path / "private-notes"
    (notes / "2024" / "01").mkdir(parents=True)
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    message = (
        f"---\ntarget_repo: github.com/example/repo\n{reserved_key}: {reserved_value}\nalert_keys: github-run:1\n---\n\n本文\n"
    )

    for messages in ([message, "正常本文"], ["正常本文", message]):
        with pytest.raises(WebInputError, match=reserved_key) as error:
            add_module.add_entries(
                notes,
                messages=messages,
                target_repo="github.com/example/repo",
                source=None,
                now=_FIXED_DT,
                entry_type=entry_type,
                question_type="yes-no" if entry_type == WI_TYPE_UWI else None,
            )
        assert error.value.next_action
        assert not list(notes.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/*.md"))


def test_add_reports_body_mismatch_when_saved_body_is_altered(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保存の処理中に本文が改変された場合、一致判定は不一致と最初の差異位置を示す。"""
    notes = tmp_path / "private-notes"
    (notes / "2024" / "01").mkdir(parents=True)
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    original_read = add_module.read_saved_entry_details  # pylint: disable=protected-access  # noqa: SLF001
    captured: dict[str, str] = {}

    def read_after_alteration(path: pathlib.Path, *, expected_body: str) -> dict[str, object | None]:
        captured["expected"] = expected_body
        path.write_text(expected_body.replace("本文", "改文", 1), encoding="utf-8")
        return original_read(path, expected_body=expected_body)

    monkeypatch.setattr(add_module, "read_saved_entry_details", read_after_alteration)
    saved_details: dict[str, dict[str, object | None]] = {}

    with pytest.raises(add_module.WebInputError) as exc_info:
        add_module.add_entries(
            notes,
            messages=[_AGENT_AWI_BODY],
            target_repo="github.com/example/repo",
            source="test",
            now=_FIXED_DT,
            saved_details=saved_details,
        )

    position = captured["expected"].index("本文") + 1
    message = str(exc_info.value)
    assert f"最初の差異: {position}文字目" in message
    assert "送信元本文:" in message
    assert "保存本文:" in message


def test_add_reports_body_match_for_trailing_newline_difference_only(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """末尾改行の有無だけが異なる保存本文は一致と判定する。"""
    notes = tmp_path / "private-notes"
    (notes / "2024" / "01").mkdir(parents=True)
    monkeypatch.setattr(_wi_sync, "repo_lock", lambda *_args, **_kwargs: contextlib.nullcontext())
    monkeypatch.setattr(_wi_sync, "pull", lambda _path: None)
    monkeypatch.setattr(_wi_sync, "commit_and_push", lambda *_args, **_kwargs: None)
    original_read = add_module.read_saved_entry_details  # pylint: disable=protected-access  # noqa: SLF001

    def read_without_trailing_newline(path: pathlib.Path, *, expected_body: str) -> dict[str, object | None]:
        path.write_text(expected_body.removesuffix("\n"), encoding="utf-8")
        return original_read(path, expected_body=expected_body)

    monkeypatch.setattr(add_module, "read_saved_entry_details", read_without_trailing_newline)
    saved_details: dict[str, dict[str, object | None]] = {}

    generated = add_module.add_entries(
        notes,
        messages=[_AGENT_AWI_BODY],
        target_repo="github.com/example/repo",
        source="test",
        now=_FIXED_DT,
        saved_details=saved_details,
    )

    assert (notes / "2024" / "01" / generated[0]).exists()


@pytest.mark.parametrize("input_kind", ["position", "body-file"])
def test_add_reports_body_match_for_crlf_inputs(
    input_kind: str,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """位置引数と本文ファイルのCRLFをLFで保存し、保存本文との一致を報告する。"""
    notes = _prepare_notes(tmp_path, monkeypatch)
    message = _AGENT_AWI_BODY.replace("\n", "\r\n")
    messages: list[str]
    if input_kind == "body-file":
        body_path = tmp_path / "body.md"
        body_path.write_bytes(message.encode())
        messages = add_module.read_body_files([str(body_path)])
    else:
        messages = [message]
    saved_details: dict[str, dict[str, object | None]] = {}

    generated = add_module.add_entries(
        notes,
        messages=messages,
        target_repo="github.com/example/repo",
        source="test",
        now=_FIXED_DT,
        saved_details=saved_details,
    )

    saved = (notes / "2024" / "01" / generated[0]).read_bytes()
    assert b"\r" not in saved
    assert (notes / "2024" / "01" / generated[0]).exists()


def test_cli_add_omits_body_verification_details(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """投入の出力は一致判定と保存本文を再掲しない。"""
    _invoke_add_body_file(monkeypatch, tmp_path, "投入本文")
    output = capsys.readouterr().out
    assert "body_match" not in output
    assert "saved_body" not in output
    assert "投入本文" not in output


def test_cli_add_outputs_source_and_extra_frontmatter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """投入の出力はsourceと非予約frontmatterを表示する。"""
    message = f"---\ncustom_key: 値\n---\n投入本文\n\n{_AGENT_AWI_SECTIONS}"
    _invoke_add_body_file(monkeypatch, tmp_path, message, "--source", "test-source")
    output = capsys.readouterr().out
    assert "    source: test-source\n" in output
    assert '    extra_frontmatter: {"custom_key": "値"}\n' in output


def test_cli_add_outputs_missing_source_and_extra_frontmatter_as_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """sourceと非予約frontmatterが無い投入は「なし」と表示する。"""
    _invoke_add_body_file(monkeypatch, tmp_path, "投入本文")
    output = capsys.readouterr().out
    assert "    source: なし\n" in output
    assert "    extra_frontmatter: なし\n" in output
