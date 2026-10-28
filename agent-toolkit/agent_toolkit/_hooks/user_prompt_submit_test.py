"""agent-toolkit/agent_toolkit/_hooks/user_prompt_submit.py のテスト。

subprocessで起動しexit code・状態ファイルの内容を検証する。
スラッシュコマンド起動時のセッション状態フラグ書き込みを網羅検証する。

"""

import json
import os
import pathlib
import re
import subprocess
from collections.abc import Callable

import pytest

from agent_toolkit._common import automated_prompt, session_state
from agent_toolkit._hooks import posttooluse, progress_self_check, user_prompt_submit
from agent_toolkit._testing import fork_runner as _fork_runner
from agent_toolkit._testing.helpers import _read_state as _read_full_state

_SCRIPTS_DIR = pathlib.Path(__file__).resolve().parents[1]
_SCRIPT = _SCRIPTS_DIR / "hook.py"
_NOTICE_PATTERN = re.compile(
    r"<atk-auto"
    r'(?=[^>]*\ssource="user_prompt_submit")'
    r'(?=[^>]*\skind="notice")[^>]*>\n',
)
_NOTICE_BOUNDARY_PATTERN = re.compile(r"</?atk-auto\b[^>]*>")
# 注記の文意は定義の読解で確かめ、ここでは通常発話へ同じ定義の本文がそのまま届くことを確かめる。
# 本文を写した期待値は文面の変更に追随させるだけで、届いた判断の正しさを検出しないため使わない。
_EXPECTED_VERIFICATION_NOTICE_BODY = posttooluse.VERIFICATION_NOTICE_BODY


@pytest.fixture(autouse=True)
def _local_plugin_root(monkeypatch):
    """この作業ツリーのhookと、その資料を同じ版で検証する。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT", raising=False)


def _read_state(state_dir: pathlib.Path, session_id: str) -> dict:
    """進捗のセルフチェックの時刻を除いたセッション状態を返す。

    セルフチェックは発話の種類によらず全てのUserPromptSubmitで時刻を記録する。
    本モジュールは発話の種類ごとの状態の違いを検証するため、その記録を比較の対象から外す。
    セルフチェック自体の記録は`progress_self_check_test.py`が検証する。
    """
    state = _read_full_state(state_dir, session_id)
    state.pop(progress_self_check.STATE_KEY, None)
    return state


def _write_state(session_id: str, state: dict) -> None:
    """hookが使う固定保存先へ、試験開始時と再設定時の状態を供給する。"""
    assert session_state.update_state(session_id, lambda _previous: state)


def _notice_bodies(context: str) -> list[str]:
    """結合された通知を分解し、標準XML境界を検証した本文の並びを返す。"""
    bodies: list[str] = []
    depth = 0
    body_start = 0
    for boundary in _NOTICE_BOUNDARY_PATTERN.finditer(context):
        if boundary.group().startswith("</"):
            assert depth > 0
            depth -= 1
            if depth == 0:
                assert context[boundary.start() - 1] == "\n"
                bodies.append(context[body_start : boundary.start() - 1])
            continue
        if depth == 0:
            opening = _NOTICE_PATTERN.match(context, boundary.start())
            assert opening is not None and opening.end() == boundary.end() + 1
            body_start = opening.end()
        depth += 1
    assert depth == 0 and bodies
    return bodies


def test_notice_bodies_preserves_outer_body_with_reordered_attributes() -> None:
    """別順の開始タグと入れ子の本文を外側の境界ごとに読む。"""
    first = (
        '<atk-auto kind="notice" source="user_prompt_submit">\n'
        '<atk-auto source="inner" kind="notice">内側</atk-auto>\n'
        "</atk-auto>"
    )
    second = '<atk-auto source="user_prompt_submit" kind="notice">\n次\n</atk-auto>'
    assert _notice_bodies(f"{first}\n{second}") == [
        '<atk-auto source="inner" kind="notice">内側</atk-auto>',
        "次",
    ]


@pytest.mark.parametrize(
    "opening,closing",
    [
        ('<other source="agent-toolkit/user_prompt_submit" kind="notice">', "</other>"),
        ('<agent-toolkit-auto-inserted kind="notice">', "</agent-toolkit-auto-inserted>"),
        ('<agent-toolkit-auto-inserted source="wrong" kind="notice">', "</agent-toolkit-auto-inserted>"),
        (
            '<agent-toolkit-auto-inserted source="agent-toolkit/user_prompt_submit" kind="wrong">',
            "</agent-toolkit-auto-inserted>",
        ),
        ('<agent-toolkit-auto-inserted source="agent-toolkit/user_prompt_submit" kind="notice">', "</other>"),
    ],
)
def test_notice_bodies_rejects_invalid_boundary(opening: str, closing: str) -> None:
    """要素名、必要属性と終了タグが違う本文を拒否する。"""
    with pytest.raises(AssertionError):
        _notice_bodies(f"{opening}\n本文\n{closing}")


def _session_title(result: subprocess.CompletedProcess[str]) -> str | None:
    """出力したsessionTitleを返す。出力自体が無い場合とその欄が無い場合はNoneを返す。"""
    if not result.stdout:
        return None
    return json.loads(result.stdout)["hookSpecificOutput"].get("sessionTitle")


def _run(
    payload: dict | str,
    *,
    state_dir: pathlib.Path,
    home_dir: pathlib.Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return _run_subcommand("user_prompt_submit", payload, state_dir=state_dir, home_dir=home_dir)


def _run_subcommand(
    subcommand: str,
    payload: dict | str,
    *,
    state_dir: pathlib.Path,
    home_dir: pathlib.Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    env = os.environ.copy()
    env["TMPDIR"] = str(state_dir)
    env["TEMP"] = str(state_dir)
    env["TMP"] = str(state_dir)
    if home_dir is not None:
        env["HOME"] = str(home_dir)
    env.update(extra_env or {})
    return _fork_runner.run_script(_SCRIPT, argv=(subcommand,), input=text, env=env)


class TestUserConfirmationSkillPrompt:
    """セッション開始後と会話圧縮後に、`agent-toolkit:user-confirmation-and-report`の起動を促す注記。

    SessionStart、UserPromptSubmit、PostToolUse(Skill)のhookを`hook.py`のサブコマンドから順に起動し、
    同スキルの内容が文脈に無い状態で届いた実ユーザー発話にだけ注記が付くことを確かめる。
    注記が欠けると、同スキルが定める公開範囲の確認などの工程を想起する手掛かりが無いまま作業が始まる。
    注記が過剰だと、起動済みの発話や機械注入のターンにも毎回同じ指示が載る。
    """

    _SKILL = "agent-toolkit:user-confirmation-and-report"

    @staticmethod
    def _session_start(
        source: str, state_dir: pathlib.Path, *, host: str = "claude", extra_env: dict[str, str] | None = None
    ) -> None:
        subcommand = "rules_context_codex" if host == "codex" else "rules_context"
        payload = {"hook_event_name": "SessionStart", "source": source, "session_id": "confirm"}
        result = _run_subcommand(subcommand, payload, state_dir=state_dir, extra_env=extra_env)
        assert result.returncode == 0, result.stderr

    @staticmethod
    def _prompt(prompt: str, state_dir: pathlib.Path, *, host: str = "claude") -> str:
        payload: dict[str, str] = {"session_id": "confirm", "prompt": prompt}
        if host == "codex":
            payload["model"] = "gpt-5"
        result = _run(payload, state_dir=state_dir)
        assert result.returncode == 0, result.stderr
        if not result.stdout:
            return ""
        return json.loads(result.stdout)["hookSpecificOutput"].get("additionalContext", "")

    @classmethod
    def _invoke_skill(cls, state_dir: pathlib.Path) -> None:
        payload = {"session_id": "confirm", "tool_name": "Skill", "tool_input": {"skill": cls._SKILL}}
        result = _run_subcommand("posttooluse", payload, state_dir=state_dir)
        assert result.returncode == 0, result.stderr

    @classmethod
    def _has_notice(cls, context: str) -> bool:
        return f"次の操作: 応答と作業の着手より前に`{cls._SKILL}`をスキル機能で起動" in context

    @pytest.mark.parametrize("source", ["startup", "clear", "compact"])
    def test_claude_notifies_once_per_context(self, source: str, tmp_path: pathlib.Path) -> None:
        """Claude Codeでも文脈ごとに起動案内を一度返し、起動の前後とも同じ案内を繰り返さない。"""
        self._session_start(source, tmp_path)
        context = self._prompt("表示が変わった原因は何か", tmp_path)
        assert self._has_notice(context)
        assert "診断より前に`agent-toolkit:bugfix`を起動" in context
        assert "説明の補足や委譲・AWIの方針も対象" in context
        assert "「起動契機」から「認可を要する操作」" in context
        assert not self._has_notice(self._prompt("続けて", tmp_path))
        self._invoke_skill(tmp_path)
        assert not self._has_notice(self._prompt("次の依頼", tmp_path))

    def test_compact_after_invocation_prompts_again(self, tmp_path: pathlib.Path) -> None:
        """会話圧縮で内容が文脈から失われるため、圧縮前に起動していても圧縮後の発話へ注記を返す。"""
        self._session_start("startup", tmp_path)
        self._invoke_skill(tmp_path)
        self._session_start("compact", tmp_path)
        assert self._has_notice(self._prompt("続けて", tmp_path))

    @pytest.mark.parametrize("host", ["claude", "codex"])
    def test_consumed_notice_is_delivered_once_again_after_compact(self, tmp_path, host):
        """圧縮した文脈には同じ固定本文を一度再配送し、後続の発話では繰り返さない。"""
        self._session_start("startup", tmp_path, host=host)
        first = self._prompt("依頼", tmp_path, host=host)
        assert self._has_notice(first)
        assert not self._has_notice(self._prompt("続けて", tmp_path, host=host))
        self._session_start("compact", tmp_path, host=host)
        restored = self._prompt("次の依頼", tmp_path, host=host)
        assert self._has_notice(restored)
        assert first == restored
        assert not self._has_notice(self._prompt("続けて", tmp_path, host=host))

    @pytest.mark.parametrize("source", ["resume", "fork"])
    def test_resume_and_fork_keep_state(self, source: str, tmp_path: pathlib.Path) -> None:
        """文脈を引き継ぐ再開と分岐では、起動済みの状態を変えない。"""
        self._session_start("startup", tmp_path)
        self._invoke_skill(tmp_path)
        self._session_start(source, tmp_path)
        assert not self._has_notice(self._prompt("続けて", tmp_path))

    @pytest.mark.parametrize("host", ["claude", "codex"])
    @pytest.mark.parametrize(
        "prompt",
        [
            "<task-notification>完了</task-notification>",
            '<atk-auto source="process-loop" kind="goal">\n継続\n</atk-auto>',
            f"{user_prompt_submit.PERIODIC_RECHECK_MARKER}\n稼働状況を確認する。",
        ],
    )
    def test_machine_injected_turn_neither_receives_nor_consumes(self, prompt: str, tmp_path: pathlib.Path, host: str) -> None:
        """機械注入のターンには注記を返さず、その後の最初の実ユーザー発話には返す。"""
        self._session_start("startup", tmp_path, host=host)
        assert not self._has_notice(self._prompt(prompt, tmp_path, host=host))
        context = self._prompt("依頼", tmp_path, host=host)
        assert self._has_notice(context)
        assert "診断より前に`agent-toolkit:bugfix`を起動" in context

    def test_usage_limit_resume_prompt_is_machine_injected(
        self, tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]
    ) -> None:
        """Function hooksの再開本文を実hookへ渡し、実ユーザー発話向けの注記と時刻を消費しない。"""
        module = _SCRIPTS_DIR.parent / "hooks" / "usage_limit_resume.ts"
        script = tmp_path / "usage-limit-prompt.mjs"
        script.write_text(
            "const {USAGE_LIMIT_RESUME_PROMPT} = await import(process.argv[2]);\n"
            "process.stdout.write(USAGE_LIMIT_RESUME_PROMPT);\n",
            encoding="utf-8",
        )
        generated = subprocess.run(
            ["node", str(script), module.as_uri()],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            env=host_environ(),
            check=False,
        )
        assert generated.returncode == 0, generated.stderr
        assert generated.stdout.startswith('<atk-auto source="usage-limit-resume" kind="notice">\n')
        self._session_start("startup", tmp_path)
        before = _read_state(tmp_path, "confirm")
        assert not self._has_notice(self._prompt(generated.stdout, tmp_path))
        assert _read_state(tmp_path, "confirm").get("last_user_prompt_at") == before.get("last_user_prompt_at")
        assert self._has_notice(self._prompt("依頼", tmp_path))

    def test_delegated_session_start_does_not_mark(self, tmp_path: pathlib.Path) -> None:
        """委譲先のSessionStartは起動待ちを記録しない。"""
        self._session_start("startup", tmp_path, extra_env={"AGENT_TOOLKIT_DELEGATED_SESSION": "1"})
        assert not self._has_notice(self._prompt("依頼", tmp_path))

    def test_user_typed_skill_command_clears(self, tmp_path: pathlib.Path) -> None:
        """ユーザー自身が同スキルをスラッシュコマンドで起動した発話には注記を返さない。"""
        self._session_start("startup", tmp_path)
        assert not self._has_notice(self._prompt(f"/{self._SKILL}", tmp_path))
        assert not self._has_notice(self._prompt("依頼", tmp_path))

    @pytest.mark.parametrize("source", ["startup", "clear", "compact"])
    def test_codex_notifies_once_per_context(self, source: str, tmp_path: pathlib.Path) -> None:
        """CodexではSkillの起動を観測できないため、文脈1つにつき最初の実ユーザー発話へ1回だけ返す。"""
        self._session_start(source, tmp_path, host="codex")
        context = self._prompt("表示が変わった原因は何か", tmp_path, host="codex")
        assert self._has_notice(context)
        assert "診断より前に`agent-toolkit:bugfix`を起動" in context
        assert "説明の補足や委譲・AWIの方針も対象" in context
        assert not self._has_notice(self._prompt("続けて", tmp_path, host="codex"))


class TestMachineInjectedTurn:
    """ユーザーが発話していないターンでの注記と時刻記録の抑止。

    実ユーザー発話が受け取るべき発話の内容を現物で確かめる手順を示す注記を機械注入ターンが消費しないようにする。
    """

    @staticmethod
    @pytest.mark.parametrize(
        ("payload_extra", "prompt", "env_extra"),
        [
            ({"source": "system"}, "状況を確認する。", {}),
            ({}, f"{user_prompt_submit.PERIODIC_RECHECK_MARKER}\n稼働状況を確認する。", {}),
            (
                {},
                '<agent-toolkit-auto-inserted source="agent-toolkit/periodic-recheck" kind="periodic-recheck">\n確認する。',
                {},
            ),
            ({}, "<task-notification>完了</task-notification>", {}),
            (
                {},
                '<agent-toolkit-auto-inserted source="agent-toolkit/process-loop" kind="goal">'
                "継続</agent-toolkit-auto-inserted>",
                {},
            ),
            (
                {},
                '<agent-toolkit-auto-inserted source="agent-toolkit/agents-server" kind="agent-delivery">'
                "委譲指示</agent-toolkit-auto-inserted>",
                {},
            ),
        ],
    )
    def test_no_notice_and_no_timestamp(
        payload_extra: dict,
        prompt: str,
        env_extra: dict,
        tmp_path: pathlib.Path,
    ) -> None:
        """機械注入ターンでは注記を返さず、直前の通常発話の時刻も更新しない。"""
        del env_extra
        sid = "machine-injected"
        result = _run({"session_id": sid, "prompt": prompt, **payload_extra}, state_dir=tmp_path)

        assert result.returncode == 0
        context = ""
        if result.stdout:
            context = json.loads(result.stdout)["hookSpecificOutput"].get("additionalContext", "")
        assert _EXPECTED_VERIFICATION_NOTICE_BODY not in context
        assert _read_state(tmp_path, sid).get("last_user_prompt_at") is None

    @staticmethod
    def test_source_user_keeps_current_behavior(tmp_path: pathlib.Path) -> None:
        """`source`が`user`の入力を実入力として観測し、注記用の時刻は記録しない。"""
        sid = "source-user"
        result = _run({"session_id": sid, "prompt": "通常の入力", "source": "user"}, state_dir=tmp_path)

        assert result.returncode == 0
        assert result.stdout == ""
        assert _read_state(tmp_path, sid).get("last_user_prompt_at") is None

    @staticmethod
    def test_normal_prompt_after_machine_turn_keeps_context_notice(tmp_path: pathlib.Path) -> None:
        """機械注入では一度の起動案内を消費せず、その後の実入力へ配送する。"""
        sid = "machine-then-user"
        _write_state(sid, {user_prompt_submit.USER_CONFIRMATION_PENDING_KEY: True})
        _run(
            {"session_id": sid, "prompt": f"{user_prompt_submit.PERIODIC_RECHECK_MARKER}\n確認する。"},
            state_dir=tmp_path,
        )
        result = _run({"session_id": sid, "prompt": "実際の依頼"}, state_dir=tmp_path)
        assert result.returncode == 0, result.stderr
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "スキル機能で起動" in context
        assert _EXPECTED_VERIFICATION_NOTICE_BODY not in context


class TestSlashCommandDetection:
    """ホストごとの手動起動でセッション状態フラグが実際の起動と一致することを検証する。"""

    def test_claude_full_plan_mode_command_does_not_record_invocation(self, tmp_path: pathlib.Path):
        sid = "full-plan-mode"
        result = _run(
            {"session_id": sid, "prompt": "/agent-toolkit:plan-mode"},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert _read_state(tmp_path, sid).get("plan_mode_skill_invoked") is None

    def test_claude_short_plan_mode_command_does_not_record_invocation(self, tmp_path: pathlib.Path):
        sid = "short-plan-mode"
        result = _run(
            {"session_id": sid, "prompt": "/plan-mode"},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert _read_state(tmp_path, sid).get("plan_mode_skill_invoked") is None

    @pytest.mark.parametrize("prompt", ["$agent-toolkit:plan-mode", "$plan-mode"])
    def test_codex_plan_mode_command_records_invocation(self, tmp_path: pathlib.Path, prompt: str) -> None:
        sid = "codex-plan-mode"
        result = _run({"session_id": sid, "prompt": prompt, "model": "gpt-5"}, state_dir=tmp_path)

        assert result.returncode == 0
        assert _read_state(tmp_path, sid).get("plan_mode_skill_invoked") is True

    def test_detects_short_skill_command_process_wi(self, tmp_path: pathlib.Path):
        sid = "short-process-wi"
        result = _run(
            {"session_id": sid, "prompt": "/process-wi"},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert _read_state(tmp_path, sid).get("process_wi_skill_invoked") is True


class TestNonMatchingPrompts:
    """スキルを起動しないプロンプトでスキル状態とsessionTitleが変わらないことの検証。"""

    @staticmethod
    def _assert_no_notice(result: subprocess.CompletedProcess[str]) -> None:
        assert result.stdout == ""

    def test_ignores_non_skill_prompt(self, tmp_path: pathlib.Path):
        sid = "non-skill"
        result = _run(
            {"session_id": sid, "prompt": "通常のユーザー発話です。"},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        self._assert_no_notice(result)
        state = _read_state(tmp_path, sid)
        assert set(state) == {"termination_evidence", "work_contract_input_sequence"}
        evidence = state["termination_evidence"]
        assert not evidence["works"]
        assert evidence["inputs"][evidence["last_input"]] == {"text": "通常のユーザー発話です。", "human": True, "offset": 0}

    @pytest.mark.parametrize(
        ("prompt", "expected_keys"),
        [
            ("<cross-session-message>継続</cross-session-message>", set()),
            (
                "<automated-prompt>継続</automated-prompt>",
                {"termination_evidence"},
            ),
        ],
    )
    def test_old_wrappers_are_not_machine_turns(self, tmp_path: pathlib.Path, prompt: str, expected_keys: set[str]):
        sid = "old-wrapper"
        result = _run({"session_id": sid, "prompt": prompt}, state_dir=tmp_path)

        assert result.returncode == 0
        assert set(_read_state(tmp_path, sid)) == expected_keys | {"work_contract_input_sequence"}

    def test_unrelated_slash_is_treated_as_user_utterance(self, tmp_path: pathlib.Path):
        """対応スキル以外のスラッシュコマンドもユーザー自身の発話として注記の対象にする。"""
        sid = "unrelated-slash"
        result = _run(
            {"session_id": sid, "prompt": "/help"},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        self._assert_no_notice(result)
        assert set(_read_state(tmp_path, sid)) == {
            "termination_evidence",
            "work_contract_input_sequence",
        }

    def test_automated_prompt_receives_no_notice(self, tmp_path: pathlib.Path):
        """process-loopが渡す起動時プロンプトは注記の対象から外し、経過時間も記録しない。"""
        sid = "automated-prompt"
        goal = automated_prompt.wrap(
            "`agent-toolkit:process-wi`を完遂してください。",
            source=automated_prompt.SOURCE_PROCESS_LOOP,
            kind=automated_prompt.KIND_GOAL,
        )
        result = _run(
            {"session_id": sid, "prompt": f"/goal {goal}"},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert result.stdout == ""
        assert _read_state(tmp_path, sid) == {}

    def test_claude_treats_codex_skill_command_as_normal_prompt(self, tmp_path: pathlib.Path):
        sid = "claude-dollar-command"
        result = _run(
            {"session_id": sid, "prompt": "$agent-toolkit:process-wi"},
            state_dir=tmp_path,
        )

        assert result.returncode == 0
        self._assert_no_notice(result)
        state = _read_state(tmp_path, sid)
        assert set(state) == {"termination_evidence", "work_contract_input_sequence"}

    def test_codex_treats_claude_skill_command_as_normal_prompt(self, tmp_path: pathlib.Path):
        sid = "codex-slash-command"
        result = _run(
            {
                "session_id": sid,
                "prompt": "/agent-toolkit:process-wi",
                "model": "gpt-5",
            },
            state_dir=tmp_path,
        )

        assert result.returncode == 0
        self._assert_no_notice(result)
        state = _read_state(tmp_path, sid)
        assert set(state) == {"termination_evidence", "work_contract_input_sequence"}

    def test_handles_empty_payload(self, tmp_path: pathlib.Path):
        """空入力・prompt欠落payloadでexit 0、状態不変。"""
        result = _run("", state_dir=tmp_path)
        assert result.returncode == 0
        sid = "no-prompt"
        result = _run({"session_id": sid}, state_dir=tmp_path)
        assert result.returncode == 0
        assert _read_state(tmp_path, sid) == {}

    def test_ignores_slash_in_middle_of_prompt(self, tmp_path: pathlib.Path):
        """先頭行以外にスラッシュコマンドがあっても対象外。"""
        sid = "slash-middle"
        result = _run(
            {
                "session_id": sid,
                "prompt": "この会話について書きます。\n/plan-mode\n(参考: 上のようにも書けます)",
            },
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert _read_state(tmp_path, sid).get("plan_mode_skill_invoked") is None

    def test_codex_normal_prompt_keeps_unrelated_state(self, tmp_path: pathlib.Path):
        sid = "codex-normal-keeps-unrelated"
        _write_state(sid, {"plan_mode_skill_invoked": True})

        result = _run(
            {"session_id": sid, "prompt": "通常のユーザー発話です。", "model": "gpt-5"},
            state_dir=tmp_path,
        )

        assert result.returncode == 0
        assert _read_state(tmp_path, sid)["plan_mode_skill_invoked"] is True

    def test_codex_process_wi_command_keeps_unrelated_state(self, tmp_path: pathlib.Path):
        sid = "codex-process-wi-keeps-unrelated"
        _write_state(sid, {"plan_mode_skill_invoked": True})

        result = _run(
            {"session_id": sid, "prompt": "$agent-toolkit:process-wi", "model": "gpt-5"},
            state_dir=tmp_path,
        )

        state = _read_state(tmp_path, sid)
        assert result.returncode == 0
        assert state["process_wi_skill_invoked"] is True
        assert state["plan_mode_skill_invoked"] is True


class TestRealignNoticeInjection:
    """実ユーザー発話の全角記号から、両ホストへ認識合わせの起動を促す。"""

    @pytest.mark.parametrize("host_fields", [{}, {"model": "gpt-test", "turn_id": "turn-test"}])
    @pytest.mark.parametrize("prompt", ["！！直してほしい", "理解が違う！！", "確認\nここです！！"])
    def test_same_realign_notice_is_delivered_once(self, tmp_path: pathlib.Path, host_fields: dict, prompt: str) -> None:
        sid = "realign-repeat"
        for index in range(3):
            result = _run({"session_id": sid, "prompt": prompt, **host_fields}, state_dir=tmp_path)
            assert result.returncode == 0
            if index:
                assert not result.stdout
                continue
            output = json.loads(result.stdout)["hookSpecificOutput"]
            bodies = _notice_bodies(output["additionalContext"])
            assert len(bodies) == 1
            assert "agent-toolkit:user-confirmation-and-report`の「認識合わせ」" in bodies[0]
            assert "次の操作:" in bodies[0]
            assert set(_read_state(tmp_path, sid)) == {
                "termination_evidence",
                "notice_fingerprints",
                "work_contract_input_sequence",
            }

    @pytest.mark.parametrize("host_fields", [{}, {"model": "gpt-test", "turn_id": "turn-test"}])
    @pytest.mark.parametrize("prompt", ["直してほしい!!", "直してほしい！", "通常の依頼"])
    def test_other_prompts_do_not_receive_realign_notice(self, tmp_path: pathlib.Path, host_fields: dict, prompt: str) -> None:
        result = _run({"session_id": "realign-negative", "prompt": prompt, **host_fields}, state_dir=tmp_path)
        assert result.returncode == 0
        assert not result.stdout

    @pytest.mark.parametrize("host_fields", [{}, {"model": "gpt-test", "turn_id": "turn-test"}])
    @pytest.mark.parametrize(
        ("extra", "prompt"),
        [
            ({"source": "system"}, "確認！！"),
            ({}, '<atk-auto source="test" kind="goal">確認！！</atk-auto>'),
            ({}, "<task-notification>完了！！</task-notification>"),
            ({}, f"{user_prompt_submit.PERIODIC_RECHECK_MARKER}\n確認！！"),
        ],
    )
    def test_machine_injection_does_not_receive_realign_notice(
        self, tmp_path: pathlib.Path, host_fields: dict, extra: dict, prompt: str
    ) -> None:
        result = _run({"session_id": "realign-machine", "prompt": prompt, **host_fields, **extra}, state_dir=tmp_path)
        assert result.returncode == 0
        assert "認識合わせ" not in result.stdout
        assert "この発話は" not in result.stdout
        assert set(_read_state(tmp_path, "realign-machine")) <= {"notice_fingerprints"}


class TestExampleInvestigationNotice:
    """例示の語と調査を求める語を併せ持つ実ユーザー発話へ、範囲語と例示を併せた依頼の項の所在を示す。

    注記が欠けると、例示の対象から自ら直列に調べ始め、例示を除いた独立した調査を委ねないまま結論へ進む。
    注記が過剰だと、貼り付けた素材や実行指示の発話にも毎回同じ本文が載る。
    """

    _PROMPT = (
        "チャット画面の消費CRのバッジの数値とクレジット詳細の数値が一致しないという報告があった。"
        "何か計上されないものがあるバグとか無いか確認して。\n"
        "例えばアプリ呼び出しの分がチャット画面の消費CRのバッジに表示されないとか？\n"
        "（報告者の勘違いの可能性もあるのでそれも含めて調査して。）"
    )
    _UTTERANCE_PATH = _SCRIPTS_DIR.parent / "skills" / "user-confirmation-and-report" / "SKILL.md"
    _ROUTING_PATH = _SCRIPTS_DIR.parent / "skills" / "delegation" / "references" / "routing.md"

    @classmethod
    def _example_bodies(cls, result: subprocess.CompletedProcess[str]) -> list[str]:
        assert result.returncode == 0, result.stderr
        if not result.stdout:
            return []
        context = json.loads(result.stdout)["hookSpecificOutput"].get("additionalContext", "")
        return [body for body in _notice_bodies(context) if str(cls._UTTERANCE_PATH) in body] if context else []

    @pytest.mark.parametrize("host_fields", [{}, {"model": "gpt-test", "turn_id": "turn-test"}])
    def test_example_with_investigation_request_receives_notice(self, tmp_path: pathlib.Path, host_fields: dict) -> None:
        """両ホストで、両資料の絶対パスと着手前の適用を求める注記を返す。"""
        result = _run({"session_id": "example-notice", "prompt": self._PROMPT, **host_fields}, state_dir=tmp_path)
        bodies = self._example_bodies(result)
        assert len(bodies) == 1
        assert str(self._ROUTING_PATH) in bodies[0]
        assert "「原文の保持と取り込み」" in bodies[0]
        assert "「コンテキスト消費が大きい調査の切り出し」" in bodies[0]
        assert "自ら調査へ着手する前に" in bodies[0]

    @pytest.mark.parametrize("host_fields", [{}, {"model": "gpt-test", "turn_id": "turn-test"}])
    @pytest.mark.parametrize("word_form", ["洗い出して", "洗い出せるか", "洗い出そう", "探して"])
    def test_investigation_word_stems_cover_inflections(
        self, tmp_path: pathlib.Path, host_fields: dict, word_form: str
    ) -> None:
        """調査を求める語の部分文字列は、活用形の発話にも注記を返す。"""
        result = _run(
            {"session_id": "example-inflections", "prompt": f"例えば通信経路を{word_form}", **host_fields},
            state_dir=tmp_path,
        )
        assert len(self._example_bodies(result)) == 1

    @pytest.mark.parametrize("host_fields", [{}, {"model": "gpt-test", "turn_id": "turn-test"}])
    @pytest.mark.parametrize(
        "prompt",
        [
            pytest.param("例えば見出しを太字にして", id="example-only"),
            pytest.param("何か計上されないものが無いか確認して", id="investigation-only"),
            pytest.param("例えば`make test`を実行して", id="execution-instruction"),
            pytest.param("このエラーを確認して\n```text\n例えば次のように表示される\n```", id="fenced-code"),
            pytest.param("`例えば`という語の扱いを確認して", id="inline-code"),
            pytest.param("> 例えば旧版ではこう書いていた\n上の引用の出典を確認して", id="quote"),
        ],
    )
    def test_other_prompts_do_not_receive_notice(self, tmp_path: pathlib.Path, host_fields: dict, prompt: str) -> None:
        """例示か調査の語の片方だけを持つ発話と、語が貼り付けた素材の中だけにある発話には返さない。"""
        result = _run({"session_id": "example-negative", "prompt": prompt, **host_fields}, state_dir=tmp_path)
        assert not self._example_bodies(result)

    @pytest.mark.parametrize(
        ("extra", "prompt_prefix", "env"),
        [
            pytest.param({"source": "system"}, "", {}, id="source"),
            pytest.param({}, f"{user_prompt_submit.PERIODIC_RECHECK_MARKER}\n", {}, id="periodic-recheck"),
            pytest.param({}, "", {"AGENT_TOOLKIT_DELEGATED_SESSION": "1"}, id="delegated"),
            pytest.param({}, "<task-notification>", {}, id="task-notification"),
            pytest.param({}, '<atk-auto source="test" kind="goal">\n', {}, id="automated-prompt"),
        ],
    )
    def test_machine_injected_turn_does_not_receive_notice(
        self, tmp_path: pathlib.Path, extra: dict, prompt_prefix: str, env: dict[str, str]
    ) -> None:
        """機械注入の5系統と委譲先のセッションには返さない。"""
        payload = {"session_id": "example-machine", "prompt": prompt_prefix + self._PROMPT, **extra}
        result = _run_subcommand("user_prompt_submit", payload, state_dir=tmp_path, extra_env=env)
        assert not self._example_bodies(result)

    @pytest.mark.parametrize("host_fields", [{}, {"model": "gpt-test", "turn_id": "turn-test"}])
    def test_other_notices_share_one_json(self, tmp_path: pathlib.Path, host_fields: dict) -> None:
        """同じターンで成立した他の2つの注記と1つの`additionalContext`へ結合する。"""
        sid = "example-combined"
        _write_state(sid, {user_prompt_submit.USER_CONFIRMATION_PENDING_KEY: True})
        result = _run({"session_id": sid, "prompt": self._PROMPT + "！！", **host_fields}, state_dir=tmp_path)
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)
        bodies = _notice_bodies(output["hookSpecificOutput"]["additionalContext"])
        assert len(bodies) == 3
        assert "agent-toolkit:user-confirmation-and-report`をスキル機能で起動" in bodies[0]
        assert "agent-toolkit:user-confirmation-and-report`の「認識合わせ」" in bodies[1]
        assert str(self._UTTERANCE_PATH) in bodies[2]


@pytest.mark.parametrize("host_fields", [{}, {"model": "gpt-test", "turn_id": "turn-test"}])
@pytest.mark.parametrize("legacy_time", [None, 0.0, 20.0])
def test_ordinary_prompts_keep_input_observation_without_reinjecting_procedure(tmp_path, host_fields, legacy_time):
    """旧時刻の有無によらず固定手順を再配送せず、実入力の観測を保つ。"""
    sid = "no-timed-procedure"
    if legacy_time is not None:
        _write_state(sid, {"last_user_prompt_at": legacy_time})
    for prompt in ("最初の依頼", "同じ条件で続ける", "次の依頼"):
        result = _run({"session_id": sid, "prompt": prompt, **host_fields}, state_dir=tmp_path)
        assert result.returncode == 0, result.stderr
        assert not result.stdout
        state = _read_state(tmp_path, sid)
        assert state.get("last_user_prompt_at") == legacy_time
        evidence = state["termination_evidence"]
        assert evidence["inputs"][evidence["last_input"]]["text"] == prompt


def test_plan_state_names_session_without_procedure_injection(tmp_path):
    """時刻による注記を撤去しても計画名をsessionTitleへ反映する。"""
    sid = "plan-title-no-procedure"
    home = tmp_path / "home"
    plan = home / ".claude" / "plans" / "current-plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text("# 計画\n", encoding="utf-8")
    _write_state(sid, {"current_plan_file_path": str(plan)})
    result = _run({"session_id": sid, "prompt": "通常の入力"}, state_dir=tmp_path, home_dir=home)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["sessionTitle"] == "current-plan"
    assert "additionalContext" not in output


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize("prompt", ["通常の入力", "/agent-toolkit:process-wi", "/process-wi"])
def test_python_hook_names_only_claude_session(tmp_path: pathlib.Path, codex: bool, prompt: str) -> None:
    """Claude CodeはPythonのcommand hookが種別と計画名を設定し、Codexはホストの命名を保つ。"""
    sid = "python-title"
    _write_state(sid, {"current_plan_file_path": "/plans/18-1921_計画.md"})
    payload = {"session_id": sid, "prompt": prompt, **({"model": "gpt-5"} if codex else {})}
    result = _run(payload, state_dir=tmp_path)
    assert result.returncode == 0
    expected = None if codex else "計画" if prompt == "通常の入力" else "process-wi 計画"
    assert _session_title(result) == expected
    assert _read_state(tmp_path, sid).get("last_hook_session_title") == expected
    assert not (tmp_path / "claude-agent-toolkit-session-title").exists()


@pytest.mark.parametrize("prefix", ["", "[SYSTEM NOTIFICATION - NOT USER INPUT]\n"])
def test_task_completion_notice_has_japanese_automated_boundary(tmp_path: pathlib.Path, prefix: str) -> None:
    """本体の包みを含む完了通知も、実ユーザー発話の注記を消費しない。"""
    result = _run(
        {"session_id": "completion-notice", "prompt": prefix + "<task-notification>Task complete</task-notification>"},
        state_dir=tmp_path,
    )
    assert result.returncode == 0
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "<atk-auto" in context
    assert "ユーザーの発話ではない" in context
    assert "日本語で書" in context
    assert "この発話は" not in context
