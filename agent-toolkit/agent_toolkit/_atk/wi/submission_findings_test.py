"""`atk wi submission end`の指摘対応記録の検査を、セッション記録と投入前レビューの返却から公開CLIで検証する。

観測した4種の欠落（発話の記録なし、追加要求だけの分類、考慮漏れの処置記録なし、レビュー指摘の脱落）を検出し、
指摘なし・選択肢どおりの確認回答・機械注入・中止・再`begin`を正常に扱うことを確かめる。
報告用要約が最終応答にあるかの検査は`_hooks/submission_report_advisor_test.py`が検証する。
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import subprocess
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import run_script
from agent_toolkit._atk.wi import submission_findings
from agent_toolkit._common import plugin_environment, session_activity
from agent_toolkit._testing.plugin_environment_support import prepare_plugin_environment

_SESSION = "11111111-2222-3333-4444-555555555555"
_TARGET = "github.com/example/myrepo"


def _later(seconds: int) -> str:
    return (datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=seconds)).isoformat()


def _user(text: str, *, seconds: int = 60) -> dict[str, Any]:
    return {"type": "user", "timestamp": _later(seconds), "message": {"role": "user", "content": text}}


def _question(answer: str, *, seconds: int = 60) -> list[dict[str, Any]]:
    options = [{"label": "既存機構へ統合"}, {"label": "新機構を追加"}]
    return [
        {
            "type": "assistant",
            "timestamp": _later(seconds),
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "AskUserQuestion",
                        "id": f"question-{seconds}",
                        "input": {"questions": [{"question": "方針", "options": options}]},
                    }
                ],
            },
        },
        {
            "type": "user",
            "timestamp": _later(seconds + 1),
            "toolUseResult": {"answers": {"方針": answer}},
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": f"question-{seconds}", "content": "回答"}],
            },
        },
    ]


@pytest.fixture(name="claude_home")
def _claude_home(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    home = tmp_path / "claude"
    (home / "projects" / "project").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    return home


def _write_session(home: pathlib.Path, entries: list[dict[str, Any]]) -> pathlib.Path:
    path = home / "projects" / "project" / f"{_SESSION}.jsonl"
    path.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    return path


def _run(*arguments: str) -> int:
    with pytest.raises(SystemExit) as exc:
        atk.main(["wi", "submission", *arguments])
    assert isinstance(exc.value.code, int)
    return exc.value.code


def _begin() -> None:
    assert _run("begin", "--session-id", _SESSION, "--target-repo", _TARGET) == 0


def _findings(tmp_path: pathlib.Path, entries: list[dict[str, Any]], *, rounds: int = 1) -> pathlib.Path:
    path = tmp_path / "findings.json"
    path.write_text(json.dumps({"confirmation_rounds": rounds, "entries": entries}, ensure_ascii=False), encoding="utf-8")
    return path


def _end(findings: pathlib.Path, *reviews: pathlib.Path) -> int:
    review_arguments = [item for review in reviews for item in ("--review-result", str(review))]
    return _run("end", "--session-id", _SESSION, "--findings-file", str(findings), *review_arguments)


def _recorded() -> bool:
    return (session_activity.submission_directory() / f"{_SESSION}.json").exists()


_OMISSION = {
    "finding": True,
    "classification": "考慮漏れ",
    "summary": "起動したリポジトリを対象から外した",
    "basis": "原文と現物から導けた",
    "adoption": "採用",
    "result": "対象へ加えた",
    "cause_analysis": "対象の判定を主題語のコード出現で行った",
    "prevention": {"decision": "採用", "handoff": "20261106-000000-001.md"},
}


def test_missing_user_utterance_entry_is_detected(
    claude_home: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """作業期間の自由記述の発話に項目が無ければ、位置を示して失敗し、記録を保持する。"""
    _begin()
    _write_session(claude_home, [_user("依頼の前置き", seconds=-60), _user("対処不要はあり得ないでしょ")])

    assert _end(_findings(tmp_path, [])) == 1
    error = capsys.readouterr().err
    assert "対処不要はあり得ないでしょ）の項目が無い" in error
    assert "依頼の前置き" not in error
    assert _recorded()


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({**_OMISSION, "classification": "追加要求"}, "4分類"),
        ({key: value for key, value in _OMISSION.items() if key != "cause_analysis"}, "cause_analysis"),
        ({**_OMISSION, "prevention": {"decision": "採用"}}, "prevention.handoff"),
        ({"finding": False}, "reason"),
    ],
)
def test_classification_and_treatment_gaps_are_detected(
    claude_home: pathlib.Path,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    entry: dict[str, Any],
    expected: str,
) -> None:
    """追加要求だけの分類、考慮漏れの原因分析・再発防止の欠落、指摘でない理由の欠落を検出する。"""
    _begin()
    path = _write_session(claude_home, [_user("改善案1つも無いの？")])
    assert _end(_findings(tmp_path, [{"source": "user", "position": _user_events_position(path), **entry}])) == 1
    assert expected in capsys.readouterr().err
    assert _recorded()


def _user_events_position(path: pathlib.Path) -> str:
    """抽出処理が返す記録位置を得る。位置の書式は`session-review-evidence --user-events`が所有する。"""
    started = session_activity.submission_started_at(_SESSION)
    assert started is not None
    events = submission_findings.user_events(_SESSION, started)
    assert len(events) == 1, (path, events)
    return f"{events[0]['record']}:{events[0]['line']}"


def test_review_finding_without_entry_is_detected(
    claude_home: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """投入前レビューの返却の指摘に項目が無ければ失敗し、同じ本文の項目があれば成功する。"""
    _begin()
    _write_session(claude_home, [])
    review = tmp_path / "review.txt"
    review.write_text(
        "判定: 修正要\n確認段階: 本文\n未解決指摘: 1\n指摘:\n- 引用した指摘に対応する処置が無い\n確認した要求と根拠: 原稿\n",
        encoding="utf-8",
    )
    assert _end(_findings(tmp_path, []), review) == 1
    assert "引用した指摘に対応する処置が無い" in capsys.readouterr().err
    entry = {**_OMISSION, "source": "review", "text": "  引用した指摘に対応する処置が無い\n"}
    assert _end(_findings(tmp_path, [entry]), review) == 0
    assert not _recorded()


def test_normal_inputs_finish_and_save_summary(
    claude_home: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """選択肢どおりの確認回答と機械注入は検査の対象外とし、指摘なしの要約を表示・保存して記録を削除する。"""
    _begin()
    _write_session(
        claude_home,
        [
            *_question("既存機構へ統合"),
            _user('<atk-auto source="agent-toolkit" kind="notice">\n通知\n</atk-auto>', seconds=70),
        ],
    )
    assert _end(_findings(tmp_path, [], rounds=2)) == 0
    out = capsys.readouterr().out
    assert "指摘の分類と処置:\n- 確認の往復: 2回\n- 指摘: なし" in out
    assert not _recorded()
    report = session_activity.read_submission_report(_SESSION)
    assert report is not None
    assert report[1] == ["指摘の分類と処置:", "- 確認の往復: 2回", "- 指摘: なし"]


def test_free_text_answer_requires_entry(claude_home: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """選択肢外の自由記述を含む確認回答は、通常の選択回答と区別して検査の対象にする。"""
    _begin()
    _write_session(claude_home, _question("対象範囲を広げて全件を対象にする"))
    assert _end(_findings(tmp_path, [])) == 1
    assert _recorded()


def test_summary_lists_each_finding(
    claude_home: pathlib.Path, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """指摘ごとの分類と再発防止策の採否・登録先を要約へ並べる。"""
    _begin()
    path = _write_session(claude_home, [_user("前提がおかしい")])
    entries = [{"source": "user", "position": _user_events_position(path), **_OMISSION}]
    assert _end(_findings(tmp_path, entries)) == 0
    assert (
        "- 起動したリポジトリを対象から外した: 考慮漏れ（再発防止: 採用、登録先: 20261106-000000-001.md）"
        in capsys.readouterr().out
    )


@pytest.mark.usefixtures("claude_home")
def test_begin_keeps_start_and_restarts_after_end() -> None:
    """同じ会話の再`begin`と投入先変更は開始時刻を保ち、`end`の後の`begin`は新しい開始時刻を付ける。"""
    _begin()
    first = session_activity.submission_started_at(_SESSION)
    assert _run("begin", "--session-id", _SESSION, "--target-repo", "github.com/example/other") == 0
    assert session_activity.submission_started_at(_SESSION) == first
    assert _run("end", "--session-id", _SESSION, "--abort") == 0
    assert not _recorded()
    _begin()
    second = session_activity.submission_started_at(_SESSION)
    assert first is not None
    assert second is not None
    assert second > first


@pytest.mark.usefixtures("claude_home")
@pytest.mark.parametrize("arguments", [[], ["--abort", "--findings-file", "/absolute/findings.json"]])
def test_end_requires_exactly_one_mode(capsys: pytest.CaptureFixture[str], arguments: list[str]) -> None:
    """正常終了と中止のどちらか一方を求め、指定が無いか両方ある場合は記録を保持して失敗する。"""
    _begin()
    assert _run("end", "--session-id", _SESSION, *arguments) == 1
    assert "--abort" in capsys.readouterr().err
    assert _recorded()


@pytest.mark.usefixtures("share_package_caches")
def test_distributed_launcher_checks_findings_without_inherited_pythonpath(
    claude_home: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """配布ランチャーの環境復元後も内部子が発話を抽出し、不足と正常終了を区別する。"""
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("VIRTUAL_ENV", None)
    environment[plugin_environment.RUNTIME_DATA_ENV] = str(tmp_path / "runtime")
    prepare_plugin_environment(run_script.PLUGIN_ROOT, environment)
    executable = run_script.PLUGIN_ROOT / "scripts" / ("atk.cmd" if os.name == "nt" else "atk")

    def invoke(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(executable), "wi", "submission", *arguments],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            encoding="utf-8",
            check=False,
            timeout=60,
        )

    begun = invoke("begin", "--session-id", _SESSION, "--target-repo", _TARGET)
    assert begun.returncode == 0, begun.stdout + begun.stderr
    path = _write_session(claude_home, [_user("前提がおかしい")])
    findings = _findings(tmp_path, [])
    rejected = invoke("end", "--session-id", _SESSION, "--findings-file", str(findings))
    assert rejected.returncode == 1, rejected.stdout + rejected.stderr
    assert "前提がおかしい）の項目が無い" in rejected.stderr
    assert "ModuleNotFoundError" not in rejected.stderr
    assert _recorded()

    findings = _findings(tmp_path, [{"source": "user", "position": _user_events_position(path), **_OMISSION}])
    accepted = invoke("end", "--session-id", _SESSION, "--findings-file", str(findings))
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "指摘の分類と処置:" in accepted.stdout
    assert "前提がおかしい" not in accepted.stderr
    assert not _recorded()
