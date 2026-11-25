"""欠陥指摘のターンで`agent-basics:bugfix`を起動せずに報告する操作を、PreToolUseとStopが同じ判定で遮断することを確かめる。"""

import json
import pathlib

import pytest

from agent_toolkit._hooks import bugfix_gate, posttooluse, stop
from agent_toolkit._hooks.pretooluse import dispatch, operation_skills
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process, codex_execution_payload

pytestmark = pytest.mark.usefixtures("without_skill_notices")

_SEND_TO_USER = "mcp__plugin_agent-toolkit_agent-toolkit__send_to_user"
_TYPED_REPORT = "再発防止: 自動検出: 送達前に検査する。"


def _human(text: str) -> dict:
    return {"type": "user", "origin": {"kind": "human"}, "message": {"content": text}}


def _tool_use(name: str, tool_input: dict) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": name, "input": tool_input}]}}


_BUGFIX = _tool_use("Skill", {"skill": "agent-basics:bugfix"})


@pytest.fixture(autouse=True)
def _main_session(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AGENT_TOOLKIT_DELEGATED_SESSION", "AGENT_TOOLKIT_OWNER_SESSION", "AGENT_TOOLKIT_HANDOFF_PATH"):
        monkeypatch.delenv(name, raising=False)


def _payload(entries: list[dict], tmp_path: pathlib.Path, **extra: object) -> dict:
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries), encoding="utf-8")
    return {"session_id": "bugfix-gate", "transcript_path": str(path), "cwd": str(tmp_path), **extra}


def _pretooluse_output(payload: dict, message: str, **send_args: str) -> dict:
    result = _run_hook_in_process(
        "pretooluse", {**payload, "tool_name": _SEND_TO_USER, "tool_input": {"message": message, **send_args}}
    )
    assert result.returncode == 0 and not result.stderr
    return json.loads(result.stdout)["hookSpecificOutput"] if result.stdout.strip() else {}


def _pretooluse_denied(payload: dict, message: str = _TYPED_REPORT, **send_args: str) -> bool:
    output = _pretooluse_output(payload, message, **send_args)
    if output.get("permissionDecision") == "deny":
        assert output["permissionDecisionReason"]
        return True
    return False


def _classified(kind: str = "是正", *, call_id: str = "understanding") -> list[dict]:
    call = _tool_use(_SEND_TO_USER, {"message": f"## 発話の理解\n分類: {kind}"})
    call["message"]["content"][0]["id"] = call_id
    result = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": call_id}]}}
    return [call, result]


def test_send_to_user_is_denied_until_bugfix_is_invoked(tmp_path: pathlib.Path) -> None:
    assert _pretooluse_denied(_payload([_human("ここがバグっている")], tmp_path))
    assert not _pretooluse_denied(_payload([_human("ここがバグっている"), *_classified(), _BUGFIX], tmp_path))


@pytest.mark.parametrize(
    ("prompt", "kind", "denied"),
    [
        ("この説明は冗長です。", "是正", True),
        ("混同を避けた例を挙げて", "非是正", False),
        ("この設計で問題ないのでは？", "非是正", False),
    ],
)
def test_classified_turn_controls_delivery_and_stop(tmp_path: pathlib.Path, prompt: str, kind: str, *, denied: bool) -> None:
    records = [_human(prompt), *_classified(kind)]
    payload = _payload(records, tmp_path)
    assert _pretooluse_denied(payload) is denied
    assert _pretooluse_denied(payload) is denied
    assert (bugfix_gate.evaluate(json.dumps(payload))[0] == "block") is denied
    assert not _pretooluse_denied(_payload([*records, _BUGFIX], tmp_path))


def test_failed_delivery_does_not_classify_and_first_success_stays_fixed(tmp_path: pathlib.Path) -> None:
    failed = _classified("非是正", call_id="failed-understanding")
    failed[1]["message"]["content"][0]["is_error"] = True
    entries = [_human("この説明は冗長です。"), *failed]
    assert "分類がない" in (bugfix_gate.send_block(_payload(entries, tmp_path), _TYPED_REPORT) or "")
    entries.extend(_classified())
    entries.extend(_classified("非是正", call_id="reclassified"))
    assert "起動していない" in (bugfix_gate.send_block(_payload(entries, tmp_path), _TYPED_REPORT) or "")


def test_stop_accepts_successfully_delivered_typed_report_only_for_current_turn(tmp_path, monkeypatch):
    records = [_human("この説明は冗長です。"), *_classified(), _BUGFIX]
    assert not _pretooluse_denied(_payload(records, tmp_path))
    assert "decision" not in _stop(records, tmp_path, monkeypatch)
    assert "decision" in _stop(records, tmp_path, monkeypatch, last_assistant_message="以上。")
    sent = _tool_use(_SEND_TO_USER, {"message": _TYPED_REPORT})
    sent["message"]["content"][0]["id"] = "report"
    records.append(sent)
    assert "decision" in _stop(records, tmp_path, monkeypatch, last_assistant_message="")
    failed = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "report", "is_error": True}]}}
    assert "decision" in _stop([*records, failed], tmp_path, monkeypatch, last_assistant_message="")
    delivered = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "report", "is_error": False}]}}
    records.append(delivered)
    assert "decision" not in _stop(records, tmp_path, monkeypatch, last_assistant_message="")
    assert "decision" not in _stop(records, tmp_path, monkeypatch, last_assistant_message="以上。", stop_hook_active=True)
    for closing in ("…", "登録リポジトリの巡回を終えました。"):
        assert "decision" not in _stop(records, tmp_path, monkeypatch, last_assistant_message=closing)
    untyped = "再発防止: 今後は注意する。"
    for active in (False, True):
        result = _stop(records, tmp_path, monkeypatch, last_assistant_message=untyped, stop_hook_active=active)
        assert result["decision"] == "block" and "種類付き" in result["reason"]
    for report in ("## 作業完了報告\n確認した。", "気付いた改善点: 別件（AWI化ステータス: 予定）"):
        assert "decision" in _stop(records, tmp_path, monkeypatch, last_assistant_message=report)
    additional = "再発防止: 確認項目: 対象に漏れがないか確認する。"
    assert "decision" not in _stop(records, tmp_path, monkeypatch, last_assistant_message=additional)
    queued = {
        "type": "attachment",
        "attachment": {"type": "queued_command", "origin": {"kind": "human"}, "prompt": "まだ長い。"},
    }
    records.extend([queued, *_classified(call_id="next"), _BUGFIX])
    assert "decision" in _stop(records, tmp_path, monkeypatch, last_assistant_message="")


def test_understanding_requires_classification(tmp_path: pathlib.Path) -> None:
    payload = _payload([_human("この説明は冗長です。")], tmp_path)
    assert _pretooluse_denied(payload, "## 発話の理解\n短くする。")
    assert not _pretooluse_denied(payload, "## 発話の理解\n分類: 是正\n短くする。")


@pytest.mark.parametrize(
    "report",
    [
        "今回は再発防止策を見送る。既存の手順に明記されているためだ。",
        "現行の手順に定められているため、再発防止の対策は追加しない。",
    ],
)
def test_correction_requires_typed_report(tmp_path, monkeypatch, report):
    entries = [_human("この説明は冗長です。"), *_classified()]
    assert _pretooluse_denied(_payload(entries, tmp_path), report)
    assert "起動していない" in _stop(entries, tmp_path, monkeypatch, last_assistant_message=report)["reason"]
    entries.append(_BUGFIX)
    for _ in range(2):
        # 規範を理由にした見送りは別の検査が担当する。  pyfltr: allow-comment
        assert bugfix_gate.send_block(_payload(entries, tmp_path), report) is None
        assert not _pretooluse_denied(_payload(entries, tmp_path), "進捗: 原因の観測を終えた。")
        assert "種類付き" in _stop(entries, tmp_path, monkeypatch, last_assistant_message=report)["reason"]
    payload = _payload(entries, tmp_path)
    body = "作業が完了した。"
    assert _pretooluse_denied(payload, body, kind="completion")
    assert not _pretooluse_denied(payload, body, kind="progress")
    assert not _pretooluse_denied(payload, body)
    assert _pretooluse_denied(payload, "## 作業完了報告\n" + body)
    assert _pretooluse_denied(payload, "再発防止: 下の7（規範の改訂）。")
    assert not _pretooluse_denied(payload)
    assert "decision" not in _stop(entries, tmp_path, monkeypatch)


@pytest.mark.parametrize(
    ("report", "denied"),
    [
        # 実例: 回帰テストを再発防止として報告し、ユーザーに是正された。  pyfltr: allow-comment
        ("再発防止: 自動検出: 今回の実際の冒頭を回帰テストに加えた。", True),
        ("再発防止: 原因の除去: 照合を外した。自動検出: 回帰テストを加えた。", True),
        ("再発防止: 自動検出: 回帰テストを追加して再現条件を固定した。", True),
        ("再発防止: 自動検出: 回帰テストを追加している。", True),
        ("再発防止: 自動検出: 異常な入力を回帰テストに加えて保存した。", True),
        ("再発防止: 自動検出: 異常な入力の回帰テストを書く。", True),
        ("再発防止: 自動検出: 今回は、異常な入力の回帰テストを書く。", True),
        ("再発防止: 自動検出: 回帰テスト（抽出結果に初回依頼が含まれること）を各AWIの完成条件に含める。", True),
        ("再発防止: 自動検出: 以前に回帰テストを追加した記録へのリンクを報告に挙げる。", False),
        ("再発防止: 設計: 回帰テストを追加していない処理も同じ検査へ集めた。", False),
        ("再発防止: 自動検出: 回帰テストを追加していた箇所を洗い出し、共通の入力検査を導入した。", False),
        ("再発防止: 自動検出: 回帰テストを追加していなかった経路にも、共通の入力検査を適用した。", False),
        ("再発防止: 自動検出: 回帰テストを追加している箇所をもとに構造を統一した。", False),
        ("処置: 直した。回帰テストを加えた。\n再発防止: 設計: 監視を外部の照会から切り離した。", False),
        (
            "再発防止: 設計: 無出力監視を外部の照会から切り離した。"
            "自動検出: 「再発防止:」の行に回帰テストを書いた送達を止めるようにした。",
            False,
        ),
    ],
)
def test_regression_test_is_not_accepted_as_prevention(tmp_path, report, denied):
    entries = [_human("直っていない。"), *_classified(), _BUGFIX]
    payload = _payload(entries, tmp_path)
    assert _pretooluse_denied(payload, report) is denied
    stop_decision, stop_reason = bugfix_gate.evaluate(json.dumps({**payload, "last_assistant_message": report}))
    assert (stop_decision == "block") is denied
    if denied:
        assert "回帰テスト" in (bugfix_gate.send_block(payload, report) or "")
        assert "回帰テスト" in stop_reason


@pytest.mark.parametrize(
    "explanation",
    [
        "これまで回帰テストを追加してきた履歴をもとに、全経路に共通する入力検査を導入した。",
        "回帰テストを追加しているだけでは再発を防げないため、共通の入力検査を導入した。",
        "以前の修正では回帰テストを追加した。今回は共通の入力検査を導入した。",
        "以前の修正では回帰テストを書いた。今回は共通の入力検査を導入した。",
        "以前、異常な入力の回帰テストを書いた。今回は共通の入力検査を導入した。",
        "以前の修正を調べた。回帰テストを追加した。今回は共通の入力検査を導入した。",
    ],
)
def test_regression_test_explanation_warns_without_blocking(tmp_path, monkeypatch, explanation):
    entries = [_human("直っていない。"), *_classified(), _BUGFIX]
    report = f"再発防止: 自動検出: {explanation}"
    send_output = _pretooluse_output(_payload(entries, tmp_path), report)
    assert send_output.get("permissionDecision") != "deny"
    assert "説明や履歴への言及ならそのまま続ける" in send_output["additionalContext"]
    stop_output = _stop(entries, tmp_path, monkeypatch, last_assistant_message=report)
    assert stop_output.get("decision") != "block"
    assert "説明や履歴への言及ならそのまま続ける" in stop_output["hookSpecificOutput"]["additionalContext"]


def test_send_to_user_without_defect_report_or_by_subagent_is_allowed(tmp_path: pathlib.Path) -> None:
    assert not _pretooluse_denied(_payload([_human("進捗を教えて"), *_classified("非是正")], tmp_path))
    assert _pretooluse_denied(_payload([_BUGFIX, _human("まだバグっている")], tmp_path))
    assert not _pretooluse_denied(_payload([_human("ここがバグっている")], tmp_path, agent_id="sub-1"))


def _stop(entries: list[dict], tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, **extra: object) -> dict:
    monkeypatch.setattr(stop, "CHECK_MODULE_NAMES", ("bugfix_gate",))
    extra.setdefault("last_assistant_message", _TYPED_REPORT)
    return stop.evaluate(json.dumps(_payload(entries, tmp_path, **extra)))


@pytest.mark.parametrize(
    ("case", "denied"),
    [
        ("initial", False),
        ("already-loaded", False),
        ("pending", True),
        ("failed", False),
        ("source-only", True),
        ("prior", True),
    ],
)
def test_repl_needs_the_host_skill_result_for_current_prompt(tmp_path, monkeypatch, case, denied):
    human = _human("ここがバグっている")
    invocation = _tool_use("Skill", {"skill": "agent-basics:bugfix"})
    invocation["isVirtual"] = True
    invocation["message"]["content"][0]["id"] = "repl_1"
    result = {
        "type": "user",
        "isVirtual": True,
        "message": {"content": [{"type": "tool_result", "tool_use_id": "repl_1", "content": "", "is_error": case == "failed"}]},
        "toolUseResult": {"success": case != "failed", "commandName": "agent-basics:bugfix"},
    }
    if case == "source-only":
        entries = [human, *_classified(), _tool_use("REPL", {"code": "tools.Skill({skill: 'agent-basics:bugfix'})"})]
    elif case == "prior":
        entries = [invocation, result, human, *_classified()]
    elif case == "pending":
        entries = [human, *_classified(), invocation]
    else:
        entries = [human, *_classified(), invocation, result]
    if case == "already-loaded":
        entries = [
            _BUGFIX,
            *entries,
            {
                "type": "api-request-blob",
                "message": {
                    "content": [
                        {"type": "text", "text": "Skill /agent-basics:bugfix is already loaded above; instructions unchanged."}
                    ]
                },
            },
        ]

    report = "bugfixの起動に失敗した。" if case == "failed" else _TYPED_REPORT
    assert _pretooluse_denied(_payload(entries, tmp_path), report) is denied
    assert (_stop(entries, tmp_path, monkeypatch, last_assistant_message=report).get("decision") == "block") is denied


@pytest.mark.parametrize(("success", "new_prompt", "denied"), [(True, False, False), (False, False, True), (True, True, True)])
def test_repl_skill_posttooluse_precedes_transcript_write(tmp_path, success, new_prompt, denied):
    human = {**_human("ここがバグっている"), "uuid": "first-human"}
    payload = _payload([human, *_classified()], tmp_path)
    skill = {
        **payload,
        "tool_name": "Skill",
        "tool_use_id": "repl_bugfix",
        "tool_input": {"skill": "agent-basics:bugfix"},
    }

    before = _run_hook_in_process("pretooluse", {**skill, "hook_event_name": "PreToolUse"})
    assert before.returncode == 0
    assert _pretooluse_denied(payload)

    if new_prompt:
        _payload([human, *_classified(), {**human, "uuid": "second-human"}], tmp_path)
    after = _run_hook_in_process(
        "posttooluse",
        {
            **skill,
            "hook_event_name": "PostToolUse",
            "tool_response": {"success": success, "commandName": "agent-basics:bugfix"},
        },
    )
    assert after.returncode == 0
    assert _pretooluse_denied(payload) is denied


def test_stop_blocks_direct_answer_without_bugfix_including_repeated_stop(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = [_human("ここがバグっている"), *_classified()]
    blocked = _stop(entries, tmp_path, monkeypatch)
    assert blocked["decision"] == "block" and "agent-basics:bugfix" in str(blocked["reason"])
    assert "起動し" in str(blocked["reason"]) and "診断してから報告してください" in str(blocked["reason"])
    assert "起動すれば同じ操作を通せる" not in str(blocked["reason"])
    assert "decision" not in _stop([*entries, *_classified(), _BUGFIX], tmp_path, monkeypatch)
    assert "decision" in _stop(entries, tmp_path, monkeypatch, stop_hook_active=True)
    assert "decision" not in _stop([_human("進捗を教えて"), *_classified("非是正")], tmp_path, monkeypatch)


@pytest.mark.parametrize(
    "correction",
    [
        "手戻り多すぎない？",
        "ちゃんと分析して直すべきでしょ。",
        "処理する手順になってなかったっけ？",
        "自分でできるはずでしょ。ユーザーの手を煩わせないで！",
        "再発防止しないとまた再発するでしょ！！",
    ],
)
def test_each_queued_correction_needs_its_own_bugfix(
    correction: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = [
        _human("ここがバグっている"),
        _BUGFIX,
        {"type": "attachment", "attachment": {"type": "queued_command", "origin": {"kind": "human"}, "prompt": correction}},
    ]
    assert _pretooluse_denied(_payload(entries, tmp_path))
    assert _stop(entries, tmp_path, monkeypatch)["decision"] == "block"
    assert not _pretooluse_denied(_payload([*entries, *_classified(), _BUGFIX], tmp_path))
    assert "decision" not in _stop([*entries, *_classified(), _BUGFIX], tmp_path, monkeypatch)


@pytest.mark.parametrize("previous_skill", [False, True])
@pytest.mark.parametrize("args,defect", [("進捗を教えて", False), ("まだバグがある", True)])
def test_command_arguments_and_bugfix_invocation_belong_to_current_turn(
    previous_skill: bool, args: str, defect: bool, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = [_human("以前のバグを直して")]
    if previous_skill:
        entries.append(_BUGFIX)
    entries.append(
        {
            "type": "user",
            "promptId": "command-turn",
            "message": {
                "content": "<command-name>/goal</command-name>\n<command-message>bugfix</command-message>\n"
                f"<command-args>{args}</command-args>"
            },
        }
    )
    entries.extend(_classified("是正" if defect else "非是正"))
    assert _pretooluse_denied(_payload(entries, tmp_path)) is defect
    assert (_stop(entries, tmp_path, monkeypatch).get("decision") == "block") is defect
    assert not _pretooluse_denied(_payload([*entries, *_classified(), _BUGFIX], tmp_path))
    assert "decision" not in _stop([*entries, *_classified(), _BUGFIX], tmp_path, monkeypatch)


@pytest.mark.parametrize("actor", ["main", "lane"])
def test_verification_failure_requires_same_actor_bugfix_before_changes(tmp_path, monkeypatch, actor):
    """初回失敗・他主体の起動・同主体の起動・新しい失敗を公開hook列で区別する。"""
    base = {"session_id": "verification-flow", "cwd": str(tmp_path), "agent_id": actor}
    failure = {
        **base,
        "hook_event_name": "PostToolUse",
        "tool_use_id": "failed-1",
        "tool_name": "Bash",
        "tool_input": {"command": "uv run --frozen pytest sample_test.py"},
        "tool_response": {"exit_code": 1},
    }
    edit: dict = {
        **base,
        "tool_name": "Edit",
        "tool_input": {"file_path": str(tmp_path / "sample.py"), "old_string": "before", "new_string": "after"},
    }
    (tmp_path / "sample.py").write_text("before", encoding="utf-8")

    def pre(*, public=False):
        if not public:
            return "verification-bugfix" in bugfix_gate.verification_notice(
                edit, edit["tool_name"], edit["tool_input"], base["session_id"]
            )
        result = _run_hook_in_process("pretooluse", edit)
        assert result.returncode == 0
        body = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
        assert body.get("permissionDecision") != "deny"
        return "verification-bugfix" in body.get("additionalContext", "")

    monkeypatch.setattr(dispatch, "_decide", lambda *_args, **_kwargs: dispatch.Decision())

    monkeypatch.setattr(operation_skills, "notices", lambda *_args, **_kwargs: [])
    assert not pre()
    posttooluse.main(json.dumps(failure))
    assert pre(public=True)
    skill = {
        **base,
        "hook_event_name": "PostToolUse",
        "tool_name": "Skill",
        "tool_input": {"skill": "agent-basics:bugfix"},
        "tool_response": "起動成功",
    }
    posttooluse.main(json.dumps({**skill, "agent_id": "other"}))
    assert bugfix_gate.read_state("verification-flow")[bugfix_gate.FAILURE_STATE_KEY][actor]["pending"]
    posttooluse.main(json.dumps(skill))
    assert not pre()
    posttooluse.main(json.dumps(failure))
    assert not pre()  # 同じ失敗の再配送で解除済みの印を戻さない。
    posttooluse.main(json.dumps({**failure, "tool_use_id": "failed-2"}))
    assert pre()


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("code,pending", [(0, False), (1, True), (2, False), (3, False), (4, False), (6, True)])
def test_ci_failure_is_distinguished_from_waiting_and_missing_runs(tmp_path, code, pending, host):
    """CI失敗1/6だけを保持し、待機の終了を不具合の失敗にしない。"""
    payload = {
        "session_id": "ci-failure",
        "tool_use_id": "ci-1",
        "hook_event_name": "PostToolUseFailure" if code else "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "atk run-script wait-ci -- --baseline baseline.json"},
        "tool_response": {"exit_code": code},
    }
    if host == "codex":
        payload.update(turn_id="ci-turn", tool_response="CIの終了状態を確認した")
        payload = codex_execution_payload(payload, tmp_path, exit_code=code)
    posttooluse.main(json.dumps(payload))
    current = bugfix_gate.read_state("ci-failure").get(bugfix_gate.FAILURE_STATE_KEY, {}).get("main", {})
    assert bool(current.get("pending")) is pending
    notice = bugfix_gate.verification_notice(payload, "Bash", {"command": "git commit -m fix"}, "ci-failure")
    assert bool(notice) is pending


@pytest.mark.parametrize("field", ["tool_use_id", "turn_id", "session_id"])
def test_ci_failure_uses_only_the_same_native_execution(tmp_path, field):
    """別の実行・ターン・主体の失敗や本文中の終了コードを、現在のCI失敗に流用しない。"""
    payload = codex_execution_payload(
        {
            "session_id": "ci-failure",
            "turn_id": "ci-turn",
            "tool_use_id": "ci-1",
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Bash",
            "tool_input": {"command": "atk run-script wait-ci -- --baseline baseline.json"},
            "tool_response": "exit_code: 1",
        },
        tmp_path,
        exit_code=1,
    )
    payload[field] = "different"
    posttooluse.main(json.dumps(payload))
    assert not bugfix_gate.read_state(payload["session_id"]).get(bugfix_gate.FAILURE_STATE_KEY)


_CORRECTION_2026_11_24 = (
    "全体的に、差分を追記するんじゃなく全体を整理しなおすべきでは？ 作業する側が混乱しやすい書き方は避けるべき"
)


@pytest.mark.parametrize(
    "prompt,gated", [(_CORRECTION_2026_11_24, True), ("この方針で進めるべきでは？", True), ("進捗を教えて", False)]
)
def test_queued_corrective_form_needs_bugfix(
    prompt: str, gated: bool, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = [
        _human("WIの原稿を直して"),
        *_classified("非是正", call_id="prior-understanding"),
        {"type": "attachment", "attachment": {"type": "queued_command", "origin": {"kind": "human"}, "prompt": prompt}},
    ]
    assert _pretooluse_denied(_payload(entries, tmp_path))
    assert "分類がない" in _stop(entries, tmp_path, monkeypatch)["reason"]
    entries.extend(_classified("是正" if gated else "非是正"))
    assert _pretooluse_denied(_payload(entries, tmp_path)) is gated
    assert ("decision" in _stop(entries, tmp_path, monkeypatch)) is gated
    assert not _pretooluse_denied(_payload([*entries, *_classified(), _BUGFIX], tmp_path))
    assert "decision" not in _stop([*entries, *_classified(), _BUGFIX], tmp_path, monkeypatch)
