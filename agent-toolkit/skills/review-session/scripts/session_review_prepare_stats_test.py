# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""セッション振り返りの試験（session_review_prepare_stats_test）。"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
import session_review_prepare as prepare

from agent_toolkit._testing import delegated_threads
from agent_toolkit._testing.session_review_prepare_support import _FIXED_NOW, _bundle_rows, _work_dir
from agent_toolkit._testing.session_review_prepare_support import _isolate_failure_ledger as _isolate_failure_ledger


def test_prepare_all_participant_costs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """全担当の原始費用と計数定義を示し、Codexのcacheを総入力へ重ねて足さない。"""
    threads = delegated_threads.write_delegated_threads(tmp_path)
    monkeypatch.setenv("HOME", str(threads.home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(threads.home / ".claude"))
    monkeypatch.setenv("CODEX_HOME", str(threads.codex_home))
    claude = threads.home / ".claude" / "projects" / "repo" / f"{delegated_threads.CLAUDE_THREAD_ID}.jsonl"
    for path, values in ((threads.transcript, (10, 7, 2, 4)), (claude, (20, 9, 3, 6))):
        usage = dict(
            zip(
                ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"), values, strict=True
            )
        )
        with path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "id": "usage",
                            "usage": usage,
                            "content": [{"type": "text", "text": "完了"}],
                        },
                    }
                )
                + "\n"
            )
    codex = next(threads.codex_home.rglob("*.jsonl"))
    usage = {
        "input_tokens": 100,
        "cached_input_tokens": 40,
        "cache_write_input_tokens": 5,
        "output_tokens": 30,
        "reasoning_output_tokens": 10,
        "total_tokens": 130,
    }
    with codex.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {"type": "token_count", "info": {"total_token_usage": usage, "last_token_usage": usage}},
                }
            )
            + "\n"
        )
    work = _work_dir(tmp_path)
    assert prepare.main(["--transcript", str(threads.transcript), "--work-dir", str(work)], now=_FIXED_NOW) == 0
    record = json.loads(capsys.readouterr().out)
    stats = [json.loads(line) for line in (work / "bundle" / "stats.jsonl").read_text(encoding="utf-8").splitlines()]
    total = next(event for event in stats if event["kind"] == "stats-total")
    assert total["tokens"] == {
        "input_tokens": 90,
        "output_tokens": 46,
        "cache_creation_input_tokens": 10,
        "cache_read_input_tokens": 50,
    }
    document = pathlib.Path(record["stats_path"]).read_text(encoding="utf-8")
    assert (
        delegated_threads.MAIN_SESSION_ID in document
        and delegated_threads.CLAUDE_THREAD_ID in document
        and delegated_threads.CODEX_THREAD_ID in document
    )
    assert '"cached_input_tokens": 40' in document and '"input_tokens": 100' in document
    assert '"input_tokens": 10' in document and '"input_tokens": 20' in document


def test_prepare_lists_adhoc_processing_per_record_without_failure_selection(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """その場のコードによる加工は記録ごとの件数と記録位置・代表入力の一覧で載り、失敗署名の選別を受けない。

    失敗した加工の呼び出しも、単発の失敗として件数表へ送られる失敗の候補とは別に加工の候補へ残る。
    """
    saved_output = "/home/u/.cache/agent-toolkit/managed-temp/atk-output-abc/output.txt"
    entries = [
        {"type": "user", "timestamp": "2026-09-06T12:00:00Z", "message": {"role": "user", "content": "初期要求"}},
        {
            "type": "assistant",
            "timestamp": "2026-09-06T12:00:01Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Bash", "id": "toolu_sed", "input": {"command": f"sed -n 2p {saved_output}"}}
                ],
            },
        },
        {
            "type": "user",
            "timestamp": "2026-09-06T12:00:02Z",
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_sed", "content": "2行目"}]},
        },
        {
            "type": "assistant",
            "timestamp": "2026-09-06T12:00:03Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "id": "toolu_py",
                        "input": {"command": "python3 -c 'import sys; sys.exit(3)'"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "timestamp": "2026-09-06T12:00:04Z",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "toolu_py", "is_error": True, "content": "Exit code 3"}],
            },
        },
    ]
    transcript = tmp_path / "11111111-2222-3333-4444-555555555555.jsonl"
    transcript.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")

    exit_code = prepare.main(["--transcript", str(transcript), "--work-dir", str(_work_dir(tmp_path))], now=_FIXED_NOW)

    assert exit_code == 0, capsys.readouterr().err
    record = json.loads(capsys.readouterr().out)
    assert record["candidate_counts"] == {"adhoc-processing": 1}
    assert record["excluded_counts"]["single-session-failure"] == 1
    candidates = pathlib.Path(record["candidates_path"]).read_text(encoding="utf-8")
    record_id = "claude:11111111-2222-3333-4444-555555555555"
    assert "- c0001 adhoc-processing（発生2件）:" in candidates
    candidate = next(item for item in _bundle_rows(record, "candidates.jsonl") if item.get("candidate_id") == "c0001")
    assert [(call["record"], call["line"], call["text"]) for call in candidate["calls"]] == [
        (record_id, 2, f"sed -n 2p {saved_output}"),
        (record_id, 4, "python3 -c 'import sys; sys.exit(3)'"),
    ]


def test_prepare_writes_breakdown_of_rate_limiting_threads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """律速区間に排他区間を持つagent threadごとに、内部の工程の内訳の節を排他区間の長い順に`stats.md`へ書く。

    内訳が無いと、振り返りは委譲先の返却本文の説明を内訳の代わりにして律速区間を見送る。
    節の記録位置は`--detail`へそのまま渡す値であり、委譲先の記録の行を指す必要がある。
    """
    threads = delegated_threads.write_delegated_threads(tmp_path)
    monkeypatch.setenv("HOME", str(threads.home))
    monkeypatch.setenv("CODEX_HOME", str(threads.codex_home))
    claude_id = delegated_threads.CLAUDE_THREAD_ID
    codex_id = delegated_threads.CODEX_THREAD_ID

    exit_code = prepare.main(["--transcript", str(threads.transcript), "--work-dir", str(_work_dir(tmp_path))], now=_FIXED_NOW)

    assert exit_code == 0, capsys.readouterr().err
    stats = pathlib.Path(json.loads(capsys.readouterr().out)["stats_path"]).read_text(encoding="utf-8")
    claude_section = f"""
## 律速threadの内訳: {claude_id}

- 排他区間: 175.0秒
- turnの経過秒: 175秒
- ツール別: Bash 2件40.0秒、Read 1件1.0秒
- 60秒以上の間隔:
  - 80.0秒: claude:{claude_id}:3〜claude:{claude_id}:4
- 反復:
  - Bash 2回（claude:{claude_id}:2、claude:{claude_id}:4）: pytest
- 遅い呼び出しの上位:
  - Bash 30.0秒 claude:{claude_id}:2: pytest
  - Bash 10.0秒 claude:{claude_id}:4: pytest
  - Read 1.0秒 claude:{claude_id}:6: /repo/a.md
"""
    codex_section = f"""
## 律速threadの内訳: {codex_id}

- 排他区間: 59.0秒
- turnの経過秒: 59秒
- ツール別: exec_command 1件20.0秒
- 60秒以上の間隔: なし
- 反復: なし
- 遅い呼び出しの上位:
  - exec_command 20.0秒 codex:{codex_id}:3: make build
"""
    assert claude_section in stats
    assert codex_section in stats
    assert stats.index(claude_section) < stats.index(codex_section)


_FIXTURE_READ_NOTICE = (
    '<atk-auto source="pretooluse" kind="warn">次の資料を、この操作の結果を使う前に指定されたReadで全文取得せよ。'
    "必須手順であり省略するな。\n未取得資料: `/plugin/skills/commit/references/message.md`。\n"
    "次の操作: Readで`/plugin/skills/commit/references/message.md`を全文読め。</atk-auto>"
)


_FIXTURE_UNAPPLIED_WARNING = (
    "警告: 設定キー`cache.path`は未適用のまま終了した（既定値を使用）。適用するには`--apply`を付けて再実行する\nexit code 0"
)


_FIXTURE_PROGRESS_NOTICE = (
    '<atk-auto source="progress-self-check" kind="notice">'
    "セッション開始から30分たった。進捗の定期セルフチェックの時刻になった。</atk-auto>"
)


_FIXTURE_DEFERRAL = (
    "読込案内を3回受けた件は、既存の未完了AWI 20260901-000000-001.md が被覆しているため、新たな対策は不要と判断した。"
)


def _fixture_entries(*, problem: bool) -> list[dict[str, Any]]:
    """振り返りの検出能力を確かめる固定記録を組み立てる。

    問題を含む版は、読込案内を読まないままの再試行（案内の反復）、終了コード0の結果に残る未適用の警告、
    既存の未完了AWIを根拠に残る原因条件を確かめずに対策を見送る完了報告、長い作業中の定期点検の未発火を持つ。
    対照版はそれぞれを正常化し、両版とも1回失敗して直したテスト（正常な回復）を持つ。
    """

    def user(time: str, content: object) -> dict[str, Any]:
        return {"type": "user", "timestamp": f"2026-09-06T{time}Z", "message": {"role": "user", "content": content}}

    def call(time: str, tool_id: str, name: str, value: dict[str, str], text: str | None = None) -> dict[str, Any]:
        content: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
        content.append({"type": "tool_use", "name": name, "id": tool_id, "input": value})
        return {"type": "assistant", "timestamp": f"2026-09-06T{time}Z", "message": {"role": "assistant", "content": content}}

    def result(time: str, tool_id: str, text: str, *, error: bool = False) -> dict[str, Any]:
        return user(time, [{"type": "tool_result", "tool_use_id": tool_id, "is_error": error, "content": text}])

    def say(time: str, text: str) -> dict[str, Any]:
        return {
            "type": "assistant",
            "timestamp": f"2026-09-06T{time}Z",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        }

    def attachment(time: str, tool_id: str, hook_name: str, notice: str) -> dict[str, Any]:
        return {
            "type": "attachment",
            "timestamp": f"2026-09-06T{time}Z",
            "attachment": {"type": "hook_additional_context", "hookName": hook_name, "toolUseID": tool_id, "content": [notice]},
        }

    commit = {"command": "git commit -F msg.txt"}
    entries = [
        user("10:00:00", "設定ファイルの読込処理を直してcommitして"),
        call("10:01:00", "t1", "Bash", {"command": "make test"}, "テストを実行する。"),
        result("10:02:00", "t1", "Error: test_config failed: KeyError 'path'", error=True),
        call("10:03:00", "t2", "Bash", {"command": "make test"}, "KeyErrorの原因を直して再実行する。"),
        result("10:04:00", "t2", "120 passed"),
    ]
    if problem:
        for index, minute in enumerate(("05", "06", "07")):
            entries += [
                call(f"10:{minute}:00", f"c{index}", "Bash", commit),
                attachment(f"10:{minute}:01", f"c{index}", "PreToolUse:Bash", _FIXTURE_READ_NOTICE),
                result(
                    f"10:{minute}:30", f"c{index}", "[main abc1234] fix: 設定の読込を直す" if index == 2 else "nothing added"
                ),
            ]
    else:
        entries += [
            call("10:05:00", "c0", "Bash", commit),
            attachment("10:05:01", "c0", "PreToolUse:Bash", _FIXTURE_READ_NOTICE),
            result("10:05:30", "c0", "pre-commit中断: 案内された資料が未取得", error=True),
            call(
                "10:06:00", "r0", "Read", {"file_path": "/plugin/skills/commit/references/message.md"}, "案内された資料を読む。"
            ),
            result("10:06:10", "r0", "# コミットメッセージ\n..."),
            call("10:07:00", "c1", "Bash", commit),
            result("10:07:30", "c1", "[main abc1234] fix: 設定の読込を直す"),
        ]
    entries += [
        call("10:08:00", "s1", "Bash", {"command": "tool sync-config"}),
        result("10:08:10", "s1", _FIXTURE_UNAPPLIED_WARNING if problem else "同期した: cache.path\nexit code 0"),
    ]
    for index, time in enumerate(("10:40", "11:10", "11:38")):
        entries += [
            call(f"{time}:00", f"w{index}", "Bash", {"command": "make lint"}, "残りの修正を続ける。"),
            result(f"{time}:20", f"w{index}", "ok"),
        ]
        if not problem and index < 2:
            entries += [
                attachment(
                    f"{time}:21",
                    f"w{index}",
                    "PostToolUse:Bash",
                    _FIXTURE_PROGRESS_NOTICE,
                ),
                say(f"{time}:30", "進捗を点検した。経過と残りの作業は見込みどおりで、反復は無い。"),
            ]
    final = (
        f"作業を完了した。{_FIXTURE_DEFERRAL}sync-configは成功した。"
        if problem
        else "作業を完了した。設定の読込を直し、テスト120件の成功とcommitを確認した。sync-configの同期も確認した。"
    )
    return [*entries, say("11:40:00", final)]


@pytest.mark.parametrize("problem", [True, False], ids=["problem", "control"])
def test_prepare_extracts_fixed_record_problems_without_naming_them(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], *, problem: bool
) -> None:
    """問題を含む固定記録では、分析に必要な根拠が候補か会話に現れ、各問題だけを正常化した対照記録では誤った候補を出さない。

    準備処理は問題名を受け取らず、通常の入力だけから抽出する。読込案内の反復は発生件数付きの候補、終了コード0の未適用の警告は
    warningの候補、正常な回復（1回の失敗と再実行の成功）は単発の失敗として候補から除く。誤った見送りと定期点検の未発火は
    抽出の対象ではなく分析判断で扱うため、判断に必要な完了報告の全文と経過時間が会話・統計へ届くことを確かめる。
    分析判断で問題を見つけられるかは、問題名を伏せた分析担当の結果を期待解答と比べる手動観測で確かめる。
    """
    transcript = tmp_path / (
        "aaaaaaaa-0000-4000-8000-000000000001.jsonl" if problem else "bbbbbbbb-0000-4000-8000-000000000002.jsonl"
    )
    transcript.write_text(
        "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in _fixture_entries(problem=problem)),
        encoding="utf-8",
    )
    work_dir = _work_dir(tmp_path)

    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work_dir)], now=_FIXED_NOW) == 0

    record = json.loads(capsys.readouterr().out)
    candidates = pathlib.Path(record["candidates_path"]).read_text(encoding="utf-8")
    conversation = pathlib.Path(record["conversation_path"]).read_text(encoding="utf-8")
    assert record["excluded_counts"]["single-session-failure"] == (1 if problem else 2)
    assert "tool-failure" not in record["candidate_counts"]
    assert record["candidate_counts"]["hook-notice"] == 1
    assert ("hook-notice（発生3件）" in candidates) is problem
    assert ("hook-notice（発生1件）" in candidates) is not problem
    assert record["candidate_counts"].get("warning", 0) == (1 if problem else 0)
    assert ("cache.path`は未適用" in candidates) is problem
    assert (_FIXTURE_DEFERRAL in conversation) is problem
    assert record["elapsed_seconds"] >= 6000
