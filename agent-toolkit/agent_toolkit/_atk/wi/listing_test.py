# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""WIのlisting: baseのテスト。"""

import datetime
import json
import pathlib
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi.layout import entry_path
from agent_toolkit._testing.wi_listing_support import _clear_agent_environment as _clear_agent_environment
from agent_toolkit.atk_test import _make_subprocess_fake, _setup_notes, _write_awi_file, _write_uwi_file


class TestListEmpty:
    """listサブコマンド: inbox空の場合は何も出力しない。"""

    def test_empty_inbox(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """inbox空時は標準出力が空であること。"""
        _setup_notes(tmp_path)
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == ""


class TestListSingle:
    """listサブコマンド: 1件のAWIを1行で出力する。"""

    def test_single_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """1件のAWIがファイル名・`target_repo`・本文冒頭要約のタブ区切り1行で出力されること。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo", body="本文1")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out == "# awi\nfb-001.md: github.com/example/foo [inbox/normal/ready] 本文1\n"

    def test_cooldown_and_invalid_cooldown_display_stable_reasons(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """期限待ちと不正期限を安定した理由で表示する。"""
        notes = _setup_notes(tmp_path)
        pending = _write_awi_file(notes, "pending.md")
        invalid = _write_awi_file(notes, "invalid.md")
        pending.write_text(
            pending.read_text(encoding="utf-8").replace(
                "type: awi\n",
                "type: awi\ncooldown_until: '2999-01-01T00:00:00+00:00'\n",
            ),
            encoding="utf-8",
        )
        invalid.write_text(
            invalid.read_text(encoding="utf-8").replace("type: awi\n", "type: awi\ncooldown_until: bad\n"),
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--skip-pull"], home=tmp_path, now=datetime.datetime.now(datetime.UTC))

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert "blocked_reason=cooldown-until cooldown_until=2999-01-01T00:00:00+00:00" in output
        assert "blocked_reason=invalid-cooldown" in output

    def test_legacy_confirmation_keeps_cooldown_reason(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """旧確認キーがあっても期限による待機理由を表示する。"""
        notes = _setup_notes(tmp_path)
        pending = _write_awi_file(notes, "pending.md")
        pending.write_text(
            pending.read_text(encoding="utf-8").replace(
                "type: awi\n",
                "type: awi\nconfirmation: required\ncooldown_until: '2999-01-01T00:00:00+00:00'\n",
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--skip-pull"], home=tmp_path, now=datetime.datetime.now(datetime.UTC))

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert "pending.md" in output
        assert "blocked_reason=cooldown-until" in output
        assert "cooldown_until=" in output

    def test_unfinished_item_with_missing_plan_file_is_listed_as_normal_awi(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """廃止した計画ファイル付きの型の未終了項目は、計画ファイルが無くても待機理由なしで着手可能と表示する。"""
        notes = _setup_notes(tmp_path)
        entry = _write_awi_file(notes, "plan.md")
        entry.write_text(
            entry.read_text(encoding="utf-8").replace(
                "type: awi\n",
                "type: awi\nplan_file: $(atk config get private_notes)/plans/2026/08/30-working-plan-a1b2.md\n",
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--skip-pull"], home=tmp_path)

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert "missing-plan-file" not in output
        assert "inbox/plan/ready" in output


class TestListMalformedFrontmatter:
    """listサブコマンド: 異常frontmatterは`(unknown)`グループへ振り分けられる。"""

    @pytest.mark.parametrize(
        ("content", "label", "expected_exit"),
        [
            ("本文のみ\n", "frontmatterなし", 0),
            ("---\ncreated: 2024\n本文\n", "閉じ区切りなし", 0),
            ("---\nstate: inbox\ncreated: 2024\n---\n\n本文\n", "target_repo欠落", 2),
        ],
    )
    def test_malformed_frontmatter_falls_back_to_unknown(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        content: str,
        label: str,
        expected_exit: int,
    ) -> None:
        """typeを確定できない異常frontmatter形式は拒否される。"""
        del label  # parametrize idのみ
        notes = _setup_notes(tmp_path)
        path = entry_path(notes, "malformed.md", fallback_date=datetime.date(2026, 10, 1))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == expected_exit
        captured = capsys.readouterr()
        if expected_exit == 0:
            assert captured.out.startswith("# unknown\n")
            assert "[inbox/frontmatter-broken/blocked]" in captured.out
            assert "blocked_reason=frontmatter-broken" not in captured.out
            assert not captured.err
        else:
            assert not captured.out
            # 終了コード2で打ち切るため、失敗行と次の操作で返す。
            assert "失敗: frontmatterのtypeが不正または欠落" in captured.err
            assert "atk wi edit" in captured.err.split("\n次の操作: ", 1)[1]

    @pytest.mark.parametrize("content", ["本文のみ\n", "---\ncreated: 2024\n本文\n"])
    def test_malformed_frontmatter_json_type_is_null(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        content: str,
    ) -> None:
        notes = _setup_notes(tmp_path)
        path = entry_path(notes, "malformed.md", fallback_date=datetime.date(2026, 10, 1))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--jsonl"], home=tmp_path)

        assert exc_info.value.code == 0
        record = json.loads(capsys.readouterr().out)
        assert record["type"] is None
        assert record["blocked_reason"] == "frontmatter-broken"


class TestListMultipleRepos:
    """listサブコマンド: 複数target_repo混在でも1件1行で全件出力される。"""

    def test_multiple_repos_grouped(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """target_repoが異なる複数のAWIがそれぞれ1行で出力される。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "fb-001.md", target_repo="github.com/example/foo")
        _write_awi_file(notes, "fb-002.md", target_repo="github.com/example/bar")
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list"], home=tmp_path)

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.splitlines() == [
            "# awi",
            "fb-001.md: github.com/example/foo [inbox/normal/ready] テスト本文",
            "fb-002.md: github.com/example/bar [inbox/normal/ready] テスト本文",
        ]

    def test_type_groups_sort_by_filename_independently_of_state(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """種別見出しを維持し、各グループ内を状態によらずファイル名順に並べる。"""
        notes = _setup_notes(tmp_path)
        _write_awi_file(notes, "z-awi.md", body="AWI")
        awi_processing = _write_awi_file(notes, "a-awi.md", body="処理中AWI")
        awi_processing.write_text(
            awi_processing.read_text(encoding="utf-8").replace("state: inbox", "state: processing"), encoding="utf-8"
        )
        _write_uwi_file(notes, "z-uwi.md", question="未回答", answer="")
        uwi_processing = _write_uwi_file(notes, "a-uwi.md", question="回答済み", answer="回答")
        uwi_processing.write_text(
            uwi_processing.read_text(encoding="utf-8").replace("state: inbox", "state: processing"), encoding="utf-8"
        )
        monkeypatch.setattr(subprocess, "run", _make_subprocess_fake([]))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "list", "--status=all"], home=tmp_path)

        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        lines = output.splitlines()
        assert [line.split(":", 1)[0] for line in lines] == [
            "# awi",
            "a-awi.md",
            "z-awi.md",
            "# uwi",
            "a-uwi.md",
            "z-uwi.md",
        ]
        assert "[processing/" in lines[1]
        assert "[inbox/" in lines[2]
        assert "[processing/answered]" in lines[4]
        assert "[inbox/unanswered]" in lines[5]
