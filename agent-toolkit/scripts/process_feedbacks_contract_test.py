"""process-feedbacksの着手時機と固定ready集合に関する文書契約テスト。

①の状態遷移の時機と固定集合の寿命は、`SKILL.md`、`pick-feedbacks.md`、`run-lanes.md`及び
`finish-session.md`へ分散して記述される。片側だけを改訂すると、選定済み項目を`inbox`のまま
②の計画・worktree作成へ渡す経路や、全レーン後にready一覧を再取得して起動時の集合を拡張する
経路が復活する。本テストは4文書が同じ対象集合、状態の意味及び遷移時機を保持することを検査する。
"""

import pathlib

_SKILL_DIR = pathlib.Path(__file__).resolve().parents[1] / "skills" / "process-feedbacks"
_SKILL = _SKILL_DIR / "SKILL.md"
_PICK = _SKILL_DIR / "references" / "pick-feedbacks.md"
_RUN_LANES = _SKILL_DIR / "references" / "run-lanes.md"
_FINISH = _SKILL_DIR / "references" / "finish-session.md"


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """指定したH2見出しから次のH2の直前までの本文を返す。"""
    body = text[text.index(f"\n## {heading}\n") + 1 :]
    end = body.find("\n## ", 1)
    return body if end < 0 else body[:end]


def test_start_processing_is_ordered_after_picker_output_review() -> None:
    """一括遷移がpicker出力の検収後に一度だけ置かれることを検査する。

    検収より前に置くと未確定の集合を移し、②側に置くと計画・worktree作成が占有より先に進む。
    """
    text = _read(_PICK)
    assert text.index("\n## 出力\n") < text.index("\n## 処理開始\n")
    assert text.count("atk mq start-processing") == 1
    assert "atk mq start-processing" in _section(text, "処理開始")


def test_start_processing_targets_only_selected_inbox_entries() -> None:
    """遷移対象が選定時点のinbox項目だけであり、再開中のprocessing項目を除くことを検査する。"""
    section = _section(_read(_PICK), "処理開始")
    assert "選定時点で`inbox`だったファイル名だけ" in section
    assert "既に`processing`だった再開項目は、この引数へ含めない" in section
    assert "全件が`processing`へ配置されたことを確認して①を完了する" in section


def test_start_processing_failure_blocks_lane_start() -> None:
    """遷移の警告・失敗・部分状態で②へ進まないことを検査する。"""
    section = _section(_read(_PICK), "処理開始")
    assert "警告、失敗又は部分状態" in section
    assert "②へ進まない" in section


def test_transition_belongs_to_step_one_across_documents() -> None:
    """①が遷移の完了まで、②が遷移済み項目の受領だけを担うことを検査する。"""
    assert "`processing`へ移すまでを①の完了条件とする" in _read(_SKILL)
    run_lanes = _read(_RUN_LANES)
    assert "`start-processing`を再実行しない" in run_lanes
    assert "atk mq start-processing" not in run_lanes


def test_fixed_ready_set_is_not_refetched_after_lanes() -> None:
    """全レーンの後始末後にready一覧を再取得しないことを検査する。"""
    for path in (_PICK, _FINISH):
        lines = [line for line in _read(path).splitlines() if "ready一覧" in line]
        assert lines, f"{path.name}にready一覧の扱いが記述されていない"
        for line in lines:
            assert "再取得せず" in line or "再取得しない" in line, f"{path.name}: {line}"
    assert "再取得" not in _section(_read(_SKILL), "実行順")
