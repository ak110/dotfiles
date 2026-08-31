"""process-feedbacksの着手時機と固定ready集合に関する文書契約テスト。

①の状態遷移の時機と固定集合の寿命は、`SKILL.md`、`share/pick-feedbacks.parent.md`、
`share/pick-feedbacks.subagent.md`、`run-lanes.md`及び`finish-session.md`へ分散して記述される。
片側だけを改訂すると、選定済み項目を`inbox`のまま②の計画・worktree作成へ渡す経路や、
全レーン後にready一覧を再取得して起動時の集合を拡張する経路、メインが選定工程の最初に
キュー一覧を取得する経路及び`blocked`項目を一律に除外する経路が復活する。
ユーザーの明示指定でpickerを省略する経路、中断再開項目の残作業調査をメインへ戻す経路、
成功時の出力へ採否理由を復活させる経路も同じ分散記述から生じる。
本テストはこれらの文書が同じ対象集合、状態の意味、遷移時機及び情報境界を保持することを検査する。
"""

import pathlib

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SKILL_DIR = _PLUGIN_ROOT / "skills" / "process-feedbacks"
_SHARE_DIR = _PLUGIN_ROOT / "share"
_SKILL = _SKILL_DIR / "SKILL.md"
_PICK = _SHARE_DIR / "pick-feedbacks.parent.md"
_PICK_SUBAGENT = _SHARE_DIR / "pick-feedbacks.subagent.md"
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
    assert text.index("\n## 出力の受領\n") < text.index("\n## 処理開始\n")
    assert text.count("atk mq start-processing") == 1
    assert "atk mq start-processing" in _section(text, "処理開始")


def test_start_processing_targets_only_selected_inbox_entries() -> None:
    """遷移対象が選定時点のinbox項目だけであり、再開中のprocessing項目を除くことを検査する。"""
    section = _section(_read(_PICK), "処理開始")
    assert "選定時点の状態を`inbox`と報告したファイル名だけ" in section
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


def test_picker_owns_queue_listing() -> None:
    """メインが選定工程の最初にキュー一覧を取得する経路の復活を検出する。"""
    assert "atk mq list --status=processable" not in _read(_SKILL)
    assert "atk mq list --status=processable" not in _read(_PICK)
    assert "atk mq list --status=processable" in _read(_PICK_SUBAGENT)


def test_picker_is_not_skipped_for_explicit_targets() -> None:
    """ユーザーの明示指定でpickerを省略する経路の復活を検出する。

    省略するとメインが本文取得と処理区分の判定を担い、自動選定と判定主体が分かれる。
    """
    text = _read(_PICK)
    assert "pickerを起動せず" not in text
    assert "pickerを省略せず" in text
    assert "選定制約" in text
    assert "当該ファイル名の一覧" in _section(text, "起動")
    assert "ユーザーが明示したファイル名の一覧" in _read(_PICK_SUBAGENT)


def test_resume_investigation_belongs_to_picker() -> None:
    """中断再開項目の残作業調査がpicker側にあり、メイン側へ戻っていないことを検査する。"""
    subagent = _read(_PICK_SUBAGENT)
    assert "resume_point" in subagent
    assert "`target_commit`以降" in subagent
    assert "レーン用worktreeとbranchの残存" in subagent
    parent = _read(_PICK)
    assert "`target_commit`以降" not in parent
    assert "レーン用worktreeとbranchの残存" not in parent


def test_picker_output_carries_lane_launch_fields_only() -> None:
    """成功時の出力が採否理由を含まず、レーン起動に必要な欄だけであることを検査する。"""
    section = _section(_read(_PICK_SUBAGENT), "出力")
    assert "reason:" not in section
    assert "decision:" not in section
    for field in ("state:", "category:", "lane:", "depends_on:", "plan_file:", "resume_point:", "terminal_order:"):
        assert field in section, field
    assert "項目別の採否理由及び対象実装の調査記録を出力へ含めない" in section


def test_main_reads_feedback_bodies_only_when_judging() -> None:
    """メインが通常経路で本文と採否理由を受け取らないことを検査する。"""
    section = _section(_read(_PICK), "出力の受領")
    assert "フィードバック本文、項目別の採否理由、対象実装の調査結果のいずれも受け取らない" in section
    assert "内容に基づく判断が実際に必要になった時点に限る" in section
    assert "メインによる本文の要約を入力の代替にしない" in _read(_RUN_LANES)


def test_picker_excludes_external_wait_only() -> None:
    """`blocked`項目を一律に除外せず、外部待ちと修復待ちだけを除外することを検査する。"""
    text = _read(_PICK_SUBAGENT)
    assert "cooldown-until" in text
    assert "dependency-unmet" in text
    assert "未回答TBD" in text
    assert "表示上の判定が`blocked`の項目" not in text
    assert "processable一覧で`blocked`の項目も" not in text
