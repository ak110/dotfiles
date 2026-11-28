from __future__ import annotations

import json
import pathlib

import pytest
import session_evidence_continuations as continuations
from agent_toolkit._common import session_registry, task_documents
from agent_toolkit._common.delegated_session import FINAL_RESPONSE_NOTICE
from agent_toolkit._common.message_format import auto_message

_PLUGIN_ROOT = pathlib.Path(continuations.__file__).resolve().parents[3]
_EXEC = task_documents.require_role_document(_PLUGIN_ROOT, "exec")
_TAIL = (
    "成果と残作業を確認し、担当内で補える作業を完遂した上で所定の返却形式を返せ。"
    "取得可能な未読・保存出力・残工程を補い、補えない不足だけを試した手段と再開条件付きで返せ。\n" + FINAL_RESPONSE_NOTICE
)
_FORMAT = "直前の返却は返却形式の契約に適合しない。形式を直して返す必要がある。診断: 成功行がありません\n" + _TAIL
_RECHECK = "直前の不足申告の前提を一度再確認する。形式違反と判定したものではない。診断: 不足の前提を再確認\n" + _TAIL


def _delegation(
    *turns: continuations.Turn, followups: tuple[str, ...] = (), role: str = "delegate"
) -> continuations.Delegation:
    if role == "exec":
        declaration = task_documents.read_declaration(_EXEC)
        assert not isinstance(declaration, str)
        resolved = continuations.Role(
            "exec",
            task=True,
            instructions=frozenset(item.instruction for item in declaration.continuations),
            result_heads=declaration.result_heads,
        )
    else:
        resolved = continuations.Role(role)
    return continuations.Delegation("codex", "chores", resolved, followups, turns)


def _turn(delivery: str | None, final: str | None, effort: str | None = "low") -> continuations.Turn:
    return continuations.Turn(delivery, "model-a", effort, final=final, seconds=2.0)


@pytest.mark.parametrize(
    ("body", "previous", "followups", "role", "expected"),
    [
        (_FORMAT, "作業の途中経過", (), "delegate", "auto_format"),
        (
            _RECHECK,
            "続行できない区分: inaccessible_input\n続行できない理由: 取得不能",
            (),
            "delegate",
            "auto_insufficiency_recheck",
        ),
        ("最後まで続けてください", "調べた範囲を返す", ("最後まで続けてください",), "delegate", "owner_continue"),
        (
            "進捗だけ教えて、追加作業はしないで",
            "調べた範囲を返す",
            ("進捗だけ教えて、追加作業はしないで",),
            "delegate",
            "unidentified",
        ),
        ("続行せず、状況だけ教えて", "調べた範囲を返す", ("続行せず、状況だけ教えて",), "delegate", "unidentified"),
        (
            "残りを続けて",
            "続行できない区分: runtime_unavailable\n続行できない理由: 失敗",
            ("残りを続けて",),
            "delegate",
            "owner_continue",
        ),
        ("Aを選ぶ", "続行できない区分: `user_decision`\n続行できない理由: 選択が要る", ("Aを選ぶ",), "delegate", "answer"),
        ("進捗を教えて", "待機中: task-1", ("進捗を教えて",), "delegate", "after_wait"),
        ("別の対象も調べて", "状態: completed\n結果: 調べた", ("別の対象も調べて",), "delegate", "new_work_or_inquiry"),
        (
            "続けて、別の件数も数えて",
            "状態: completed\n結果: 数えた",
            ("続けて、別の件数も数えて",),
            "delegate",
            "unidentified",
        ),
        ("実装開始", "計画作成完了\n計画: 済", ("実装開始",), "exec", "declared_stage"),
        ("続きをやって", "古い版の結果行", ("続きをやって",), "exec", "unidentified"),
        ("あなたが起動した子は終了した。\n終了したsession/thread: x", "作業中", (), "delegate", "after_wait"),
        ("直前のturnはモデルの過負荷（serverOverloaded）で中断した。", "作業中", (), "delegate", "infra_resume"),
        ("出所を示さない入力", "作業中", (), "delegate", "unidentified"),
        ("続けて。" + _FORMAT, "作業の途中経過", ("続けて。" + _FORMAT,), "delegate", "unidentified"),
        (
            "「担当内で補える作業を完遂した上で所定の返却形式を返せ。」の意味は？",
            "作業の途中経過",
            (),
            "delegate",
            "unidentified",
        ),
        ("直前の返却を受けて。" + _TAIL, "作業の途中経過", (), "delegate", "auto_unknown"),
        (
            "未完了の範囲を教えてください。",
            "調べた範囲を返す",
            ("未完了の範囲を教えてください。",),
            "delegate",
            "new_work_or_inquiry",
        ),
        (
            "別の案件の残りを調べてください。",
            "調べた範囲を返す",
            ("別の案件の残りを調べてください。",),
            "delegate",
            "unidentified",
        ),
        (
            "別の案件の残りを続けてください。",
            "調べた範囲を返す",
            ("別の案件の残りを続けてください。",),
            "delegate",
            "unidentified",
        ),
        ("続けてもらえますか？", "調べた範囲を返す", ("続けてもらえますか？",), "delegate", "unidentified"),
        ("残りが気になる", "調べた範囲を返す", ("残りが気になる",), "delegate", "unidentified"),
        (
            "B案件の調査を続けてください。",
            "A案件を途中まで確認した",
            ("B案件の調査を続けてください。",),
            "delegate",
            "unidentified",
        ),
        (
            "「続けて」を英訳してください。",
            "A案件を途中まで確認した",
            ("「続けて」を英訳してください。",),
            "delegate",
            "unidentified",
        ),
        (
            "では、残りを最後まで続けてください。",
            "A案件を途中まで確認した",
            ("では、残りを最後まで続けてください。",),
            "delegate",
            "owner_continue",
        ),
    ],
    ids=[
        "auto-format",
        "auto-recheck",
        "owner-after-partial",
        "inquiry-after-partial",
        "negated-cue-after-partial",
        "owner-after-runtime",
        "answer",
        "after-wait",
        "new-work",
        "cue-after-completed",
        "declared-stage",
        "unmatched-role-head",
        "child-resume",
        "infra-resume",
        "unknown-source",
        "owner-quotes-auto",
        "quoted-marker-without-server-end",
        "auto-without-known-reason",
        "inquiry-with-cue-after-partial",
        "other-work-with-cue-after-partial",
        "other-work-with-order-after-partial",
        "question-with-order-after-partial",
        "cue-without-order-after-partial",
        "other-target-with-order-after-partial",
        "quoted-order-after-partial",
        "pure-order-with-lead-after-partial",
    ],
)
def test_classify_input_separates_sources_and_reasons(body, previous, followups, role, expected) -> None:
    assert continuations.classify_input(body, previous, _delegation(followups=followups, role=role)) == expected


def test_same_text_from_server_and_owner_is_not_attributed_to_the_owner() -> None:
    groups, _summary = continuations.tally(
        [
            _delegation(
                _turn("依頼", "途中で返した"),
                _turn(_FORMAT, "まだ途中"),
                _turn(_FORMAT, "状態: completed"),
                followups=(_FORMAT,),
            )
        ]
    )

    group = groups[("codex", "model-a", "low", "chores", "delegate")]
    assert group["continuations"]["owner_continue"] == 0
    assert group["unidentified"] == 2


def test_contract_update_notice_does_not_hide_a_text_collision() -> None:
    notice = auto_message("作業契約へ反映する。", source="agents-server", kind="work-contract-update")
    groups, _summary = continuations.tally(
        [
            _delegation(
                _turn("依頼", "途中で返した"),
                _turn(_FORMAT, "まだ途中"),
                _turn(_FORMAT + "\n\n" + notice, "状態: completed"),
                followups=(_FORMAT,),
            )
        ]
    )

    assert groups[("codex", "model-a", "low", "chores", "delegate")]["unidentified"] == 2


def test_return_state_ignores_quoted_examples_and_undefined_classes() -> None:
    role = continuations.Role("delegate")
    quoted = "状態: completed\n結果: 書式の例\n```text\n続行できない区分: private_fragment\n続行できない理由: 例\n```"
    assert continuations.return_state(quoted, role) == "completed"
    assert (
        continuations.return_state("続行できない区分: private_fragment\n続行できない理由: x", role) == "cannot_continue:invalid"
    )
    assert continuations.return_state("exit 0\n出力", continuations.Role("shell")) == "unknown"
    assert continuations.return_state("結果: 済\n続行できない区分: permission_denied", role) == "no_success_line"
    separated = "結果: 済\n続行できない区分: permission_denied\n\n  続行できない理由: "
    assert continuations.return_state(separated, role) == "no_success_line"


def test_delivered_text_removes_only_the_trailing_contract_notice() -> None:
    notice = auto_message("作業契約へ反映する。", source="agents-server", kind="work-contract-update")
    body = "例として次の通知が付く:\n\n" + notice + "\n残りを続けて"
    assert continuations.delivered_text(body + "\n\n" + notice) == body


def test_wait_statement_inside_longer_return_is_not_a_wait() -> None:
    role = continuations.Role("delegate")
    assert continuations.return_state("待機中: task-1", role) == "wait"
    assert continuations.return_state("途中経過\n待機中: task-1", role) == "no_success_line"


def test_tally_attributes_each_event_to_the_turn_that_returned() -> None:
    delegation = _delegation(
        _turn("依頼", "途中で返した", effort="low"),
        _turn(_FORMAT, "続行できない区分: owner_input\n続行できない理由: 値が要る", effort="medium"),
        _turn("値はAです", "状態: completed", effort="high"),
        followups=("値はAです",),
    )

    groups, summary = continuations.tally([delegation])

    low = groups[("codex", "model-a", "low", "chores", "delegate")]
    medium = groups[("codex", "model-a", "medium", "chores", "delegate")]
    high = groups[("codex", "model-a", "high", "chores", "delegate")]
    assert low["continuations"]["auto_format"] == 1
    assert low["auto_left_work_unidentified"] == 1
    assert medium["auto_left_work_unidentified"] == 0
    assert low["delegations_with_continuation"] == 1
    assert low["continuation_turn_seconds"] == 2.0
    assert medium["uncounted"]["answer"] == 1
    assert sum(medium["continuations"].values()) == 0
    assert high["final_returns"] == 1
    assert [group["returns"] for group in (low, medium, high)] == [1, 1, 1]
    assert [group["delegations"] for group in (low, medium, high)] == [1, 1, 1]
    assert summary["delegations_with_continuation"] == 1
    assert summary["delegations_incomplete_without_continuation"] == 0


def test_tally_counts_one_continuation_per_return_and_keeps_missing_effort() -> None:
    delegation = _delegation(
        _turn("依頼", "途中で返した", effort=None),
        _turn(_FORMAT, None),
        _turn("最後まで続けて", "状態: completed"),
        followups=("最後まで続けて",),
    )

    groups, summary = continuations.tally([delegation])

    missing = groups[("codex", "model-a", None, "chores", "delegate")]
    assert missing["continuations"]["auto_format"] == 1
    assert missing["continuations"]["owner_continue"] == 0
    assert missing["uncounted"]["duplicate_for_same_return"] == 1
    assert missing["delegations_with_continuation"] == 1
    assert summary["delegations_with_continuation"] == 1


def test_tally_separates_incomplete_without_continuation() -> None:
    groups, summary = continuations.tally(
        [
            _delegation(_turn("依頼", "続行できない区分: permission_denied\n続行できない理由: 拒否")),
            _delegation(_turn("依頼", "状態: completed")),
            _delegation(_turn("依頼", "待機中: task-1")),
        ]
    )

    group = groups[("codex", "model-a", "low", "chores", "delegate")]
    assert group["final_without_continuation"] == {"cannot_continue:permission_denied": 1}
    assert group["final_returns"] == 3
    assert summary["delegations_incomplete_without_continuation"] == 1


def test_tally_does_not_call_undetermined_delegations_uncontinued() -> None:
    _groups, summary = continuations.tally(
        [
            _delegation(_turn("依頼", None)),
            _delegation(_turn("依頼", "途中"), _turn("出所を示さない入力", "途中"), followups=()),
            _delegation(_turn("依頼", "古い版の結果行"), role="exec"),
        ]
    )

    assert summary["delegations_without_return"] == 1
    assert summary["delegations_incomplete_with_unidentified_input"] == 1
    assert _groups[("codex", "model-a", "low", "chores", "delegate")]["final_incomplete_with_unidentified_input"] == 1
    assert summary["delegations_final_state_unknown"] == 1
    assert summary["delegations_incomplete_without_continuation"] == 0


def _codex_entry(kind: str, payload: dict) -> dict:
    return {"timestamp": "2026-09-06T12:00:00Z", "type": kind, "payload": payload}


def _codex_input(text: str) -> dict:
    return _codex_entry("response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]})


def _codex_turn(turn_id: str, effort: str | None, inputs: list[str], final: str, model: str | None = "model-a") -> list[dict]:
    context = {"turn_id": turn_id} | ({"model": model} if model else {}) | ({"effort": effort} if effort else {})
    return [
        _codex_entry("event_msg", {"type": "task_started", "turn_id": turn_id, "root_turn_id": turn_id}),
        _codex_entry("turn_context", context),
        *[_codex_input(item) for item in inputs],
        _codex_entry(
            "response_item",
            {
                "type": "message",
                "role": "assistant",
                "phase": "commentary",
                "content": [{"type": "output_text", "text": "途中の発話"}],
            },
        ),
        _codex_entry(
            "event_msg", {"type": "task_complete", "turn_id": turn_id, "last_agent_message": final, "duration_ms": 1500}
        ),
    ]


def _write_jsonl(path: pathlib.Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")


def _deliver(body: str) -> str:
    return auto_message(body, source="agents-server", kind="delivery")


def _claude_user(content: str | list[dict]) -> dict:
    return {"type": "user", "message": {"role": "user", "content": content}}


def _claude_assistant(model: str, effort: str | None, content: list[dict]) -> dict:
    return {"type": "assistant", "effort": effort, "message": {"role": "assistant", "model": model, "content": content}}


def _publish(session_id: str, engine: str, turn_seq: int, launch_info: session_registry.LaunchInfo, **kwargs) -> None:
    session_registry.publish(
        session_id,
        state=session_registry.PublicationState.RESULT_COMPLETED,
        engine=engine,
        cwd="/work",
        model_type="chores",
        turn_seq=turn_seq,
        launch_info=launch_info,
        **kwargs,
    )


def test_main_counts_registered_records_without_exposing_bodies(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    codex_home = tmp_path / "codex"
    claude_home = tmp_path / "claude"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
    codex_id = "019a0000-1111-7222-8333-444444444441"
    claude_id = "019a0000-1111-7222-8333-444444444442"
    missing_id = "019a0000-1111-7222-8333-444444444443"
    outside_id = "019a0000-1111-7222-8333-444444444444"
    unobserved_id = "019a0000-1111-7222-8333-444444444445"
    secret = "秘匿したい依頼本文"
    _write_jsonl(
        codex_home / "sessions/2026/09/06" / f"rollout-2026-09-06T12-00-00-{codex_id}.jsonl",
        [
            *_codex_turn("t1", "low", [_deliver(secret)], "調べた範囲を返す"),
            *_codex_turn("t2", "medium", [_deliver(_FORMAT), _deliver("実行中に届いた訂正")], "状態: completed", model=None),
        ],
    )
    _publish(
        codex_id,
        "codex",
        2,
        session_registry.LaunchInfo(
            prompt=secret, created_at="2026-09-06T12:00:00+00:00", followup_prompts=("実行中に届いた訂正",)
        ),
        launch_kind="explore",
    )
    _write_jsonl(
        claude_home / "projects/project" / f"{claude_id}.jsonl",
        [
            _claude_user(_deliver(secret)),
            _claude_assistant("model-b", "low", [{"type": "tool_use", "id": "call-1", "name": "Bash", "input": {}}]),
            _claude_user(_deliver("実行中の訂正")),
            _claude_user([{"type": "tool_result", "tool_use_id": "call-1", "content": "ok"}]),
            _claude_user(_deliver("道具の結果の直後の訂正")),
            _claude_assistant("model-b", "low", [{"type": "text", "text": "途中"}]),
            _claude_assistant("<synthetic>", None, [{"type": "text", "text": "合成"}]),
            {**_claude_assistant("model-side", "max", [{"type": "text", "text": "別の文脈"}]), "isSidechain": True},
            _claude_user(_deliver("最後まで続けて")),
        ],
    )
    _publish(
        claude_id,
        "claude",
        2,
        session_registry.LaunchInfo(
            prompt=secret,
            created_at="2026-09-06T12:00:00+00:00",
            followup_prompts=("実行中の訂正", "道具の結果の直後の訂正", "最後まで続けて"),
            final_responses=((1, "途中で返した"), (2, "状態: completed")),
        ),
        model="registry-model",
        effort="high",
    )
    _publish(missing_id, "codex", 1, session_registry.LaunchInfo(created_at="2026-09-06T12:00:00+00:00"))
    _publish(outside_id, "codex", 1, session_registry.LaunchInfo(created_at="2026-08-01T00:00:00+00:00"))
    _write_jsonl(
        claude_home / "projects/project" / f"{unobserved_id}.jsonl",
        [
            _claude_user(_deliver(secret)),
            _claude_assistant("<synthetic>", None, [{"type": "text", "text": "合成"}]),
            _claude_user(_deliver("観測の無い後続")),
        ],
    )
    _publish(
        unobserved_id,
        "claude",
        2,
        session_registry.LaunchInfo(
            created_at="2026-09-06T12:00:00+00:00",
            followup_prompts=("観測の無い後続",),
            final_responses=((1, "続行できない区分: runtime_unavailable\n続行できない理由: 失敗"), (2, "状態: completed")),
        ),
        model="registry-model",
        effort="high",
    )

    assert continuations.main(["--since", "2026-09-06", "--until", "2026-09-06"]) == 0

    raw = capsys.readouterr().out
    assert secret not in raw and codex_id not in raw and str(codex_home) not in raw
    result = json.loads(raw)
    assert result["registered_in_period"] == 4
    assert result["analyzed_delegations"] == 3
    assert result["excluded"] == {"record_unresolved": 1}
    assert result["delegations_with_continuation"] == 2
    rows = {(row["engine"], row["model"], row["effort"]): row for row in result["groups"]}
    codex_low = rows[("codex", "model-a", "low")]
    assert codex_low["continuations"]["auto_format"] == 1
    assert codex_low["continuation_turn_seconds"] == 1.5
    assert rows[("codex", None, "medium")]["final_returns"] == 1
    claude_low = rows[("claude", "model-b", "low")]
    assert claude_low["role"] == "delegate"
    assert claude_low["continuations"]["owner_continue"] == 1
    assert claude_low["continuation_turn_seconds_missing"] == 1
    assert rows[("claude", None, None)]["unidentified"] == 1
    assert {key[1] for key in rows} == {"model-a", "model-b", None}


def _end(text: str) -> dict:
    entry = _claude_assistant("model-b", "low", [{"type": "text", "text": text}])
    entry["message"]["stop_reason"] = "end_turn"
    return entry


@pytest.mark.parametrize(
    ("entries", "turn_seq", "expected"),
    [
        (
            [
                _claude_user(_deliver("依頼")),
                _claude_assistant("model-b", "low", [{"type": "text", "text": "考え中"}]),
                _claude_user(_deliver("実行中の訂正")),
                _end("途中で返した"),
                _claude_user(_deliver("最後まで続けて")),
            ],
            2,
            ["依頼", "最後まで続けて"],
        ),
        (
            [
                _claude_user(_deliver("依頼")),
                _claude_assistant("model-b", "low", [{"type": "text", "text": "途中で返した"}]),
                _claude_user(_deliver("最後まで続けて")),
            ],
            2,
            ["依頼", "最後まで続けて"],
        ),
        (
            [
                _claude_user(_deliver("依頼")),
                _claude_assistant("model-b", "low", [{"type": "tool_use", "id": "call-1", "name": "Bash", "input": {}}]),
                {**_claude_assistant("<synthetic>", None, [{"type": "text", "text": "API Error"}]), "isApiErrorMessage": True},
                _claude_user(_deliver("最後まで続けて")),
            ],
            2,
            ["依頼", "最後まで続けて"],
        ),
        (
            [
                _claude_user(_deliver("依頼")),
                _claude_assistant("model-b", "low", [{"type": "tool_use", "id": "call-1", "name": "Bash", "input": {}}]),
                _claude_user(_deliver("実行中の訂正")),
            ],
            2,
            None,
        ),
        (
            [
                _claude_user(_deliver("依頼")),
                _claude_assistant("model-b", "low", [{"type": "text", "text": "考え中"}]),
                _claude_user(_deliver("実行中の訂正")),
                _end("途中で返した"),
                _claude_assistant("<synthetic>", None, [{"type": "text", "text": "通知"}]),
                _claude_user(_deliver("最後まで続けて")),
            ],
            2,
            ["依頼", "最後まで続けて"],
        ),
    ],
    ids=["steer-before-end-turn", "unobserved-end", "api-error-end", "steer-with-missing-delivery", "synthetic-after-end"],
)
def test_claude_turn_boundaries_follow_observed_ends(tmp_path, entries, turn_seq, expected) -> None:
    path = tmp_path / "record.jsonl"
    _write_jsonl(path, entries)

    turns = continuations._claude_turns(path, turn_seq)  # pylint: disable=protected-access

    assert (None if turns is None else [turn.delivery for turn in turns]) == expected


def test_auto_recovery_prefixes_match_the_server_source() -> None:
    source = (_PLUGIN_ROOT / "agent_toolkit/_agents_server/manager_resume.py").read_text(encoding="utf-8")
    for text in (
        continuations.AUTO_FORMAT_PREFIX,
        continuations.AUTO_RECHECK_PREFIX,
        continuations.AUTO_RECOVERY_MARKER,
        continuations.CHILD_RESUME_PREFIX,
    ):
        assert text.removesuffix("\n") in source
