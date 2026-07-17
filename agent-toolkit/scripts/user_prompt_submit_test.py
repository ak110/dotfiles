"""agent-toolkit/scripts/user_prompt_submit.py のテスト。

subprocessで起動しexit code・状態ファイルの内容を検証する。
スラッシュコマンド起動時のセッション状態フラグ書き込みを網羅検証する。

規範照会・是正要求検出（`_norm_inquiry_escalation.py`の`_match_norm_inquiry_escalation`への委譲、
クールダウン判定）は`main()`公開経路（フック実行経路）経由でのみ検証する。
`_match_norm_inquiry_escalation`単体への直接テストは設けない。
検出語の具体入力は隔離フィクスチャ`_norm_inquiry_escalation_test_inputs.txt`から
`_load_norm_inquiry_inputs`経由で読み込み、テストコード本文へ直接転記しない
（`agent-toolkit:agent-standards`「コンテキスト汚染の回避」節）。
"""

import json
import os
import pathlib
import subprocess

import _fork_runner

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "user_prompt_submit.py"
_NORM_INQUIRY_INPUTS_PATH = pathlib.Path(__file__).resolve().parent / "_norm_inquiry_escalation_test_inputs.txt"

# クールダウン閾値（`user_prompt_submit.py`の`_NORM_INQUIRY_COOLDOWN_TURNS`と同値）。
# 隔離フィクスチャからの動的読み込みとは独立した定数であり、
# 実装側の定数値が変わった場合は本テストも同期見直しの対象とする。
_COOLDOWN_TURNS = 5


def _load_norm_inquiry_inputs() -> dict[str, list[str]]:
    """隔離フィクスチャからカテゴリ別のテスト入力を読み込む。

    フォーマットは`<category>\\t<text>`のタブ区切り。空行と`#`先頭行はスキップする。
    戻り値はカテゴリごとの入力テキスト一覧の辞書
    （`norm-inquiry`・`correction-request`・非該当を示す`none`）。
    """
    by_category: dict[str, list[str]] = {}
    for raw in _NORM_INQUIRY_INPUTS_PATH.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "\t" not in stripped:
            continue
        category, text = stripped.split("\t", 1)
        by_category.setdefault(category.strip(), []).append(text.strip())
    return by_category


_NORM_INQUIRY_INPUTS = _load_norm_inquiry_inputs()


def _run(
    payload: dict | str,
    *,
    state_dir: pathlib.Path,
) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    env = os.environ.copy()
    env["TMPDIR"] = str(state_dir)
    env["TEMP"] = str(state_dir)
    env["TMP"] = str(state_dir)
    return _fork_runner.run_script(_SCRIPT, input=text, env=env)


def _read_state(state_dir: pathlib.Path, session_id: str) -> dict:
    path = state_dir / f"claude-agent-toolkit-{session_id}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _additional_context(result: subprocess.CompletedProcess[str]) -> str | None:
    """標準出力の`hookSpecificOutput.additionalContext`を取り出す。未出力時は`None`。"""
    stdout = result.stdout.strip()
    if not stdout:
        return None
    payload = json.loads(stdout)
    return payload.get("hookSpecificOutput", {}).get("additionalContext")


def _prime_counter(sid: str, state_dir: pathlib.Path, *, turns: int) -> None:
    """`user_prompt_counter`を非検出プロンプトの反復送信で指定回数まで進める。"""
    non_matching = _NORM_INQUIRY_INPUTS["none"][0]
    for _ in range(turns):
        _run({"session_id": sid, "prompt": non_matching}, state_dir=state_dir)


class TestSlashCommandDetection:
    """スラッシュコマンド起動時のセッション状態フラグ書き込み検証。"""

    def test_detects_full_skill_command_plan_mode(self, tmp_path: pathlib.Path):
        sid = "full-plan-mode"
        result = _run(
            {"session_id": sid, "prompt": "/agent-toolkit:plan-mode"},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert _read_state(tmp_path, sid).get("plan_mode_skill_invoked") is True

    def test_detects_short_skill_command_plan_mode(self, tmp_path: pathlib.Path):
        sid = "short-plan-mode"
        result = _run(
            {"session_id": sid, "prompt": "/plan-mode"},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert _read_state(tmp_path, sid).get("plan_mode_skill_invoked") is True

    def test_detects_short_skill_command_process_feedbacks(self, tmp_path: pathlib.Path):
        sid = "short-process-feedbacks"
        result = _run(
            {"session_id": sid, "prompt": "/process-feedbacks"},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert _read_state(tmp_path, sid).get("process_feedbacks_skill_invoked") is True

    def test_detects_short_skill_command_session_review(self, tmp_path: pathlib.Path):
        """短縮名`/session-review`もフルスキル名キーで正規化して保存する。"""
        sid = "short-session-review"
        result = _run(
            {"session_id": sid, "prompt": "/session-review"},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        invoked = _read_state(tmp_path, sid).get("session_review_invoked")
        assert isinstance(invoked, dict)
        assert invoked.get("agent-toolkit:session-review") is True


class TestNonMatchingPrompts:
    """非スキル起動プロンプトでフラグが立たないことの検証。

    非スラッシュコマンド入力は規範照会・是正要求検出の対象経路（`user_prompt_counter`加算）を
    通るため、当該2ケースは状態辞書の完全一致ではなくスキルフラグ・追加出力の不在で検証する
    （`/help`は既存のスラッシュコマンド検出処理に留まりカウンター加算対象外のため完全一致のまま維持する）。
    """

    def test_ignores_non_skill_prompt(self, tmp_path: pathlib.Path):
        sid = "non-skill"
        result = _run(
            {"session_id": sid, "prompt": "通常のユーザー発話です。"},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert _additional_context(result) is None
        state = _read_state(tmp_path, sid)
        assert "plan_mode_skill_invoked" not in state
        assert "session_review_invoked" not in state
        assert "process_feedbacks_skill_invoked" not in state

    def test_ignores_unrelated_slash(self, tmp_path: pathlib.Path):
        sid = "unrelated-slash"
        result = _run(
            {"session_id": sid, "prompt": "/help"},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert _read_state(tmp_path, sid) == {}

    def test_handles_empty_payload(self, tmp_path: pathlib.Path):
        """空入力・prompt欠落payloadでexit 0、状態不変。"""
        # 空入力
        result = _run("", state_dir=tmp_path)
        assert result.returncode == 0
        # prompt欠落
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
        assert _additional_context(result) is None
        assert _read_state(tmp_path, sid).get("plan_mode_skill_invoked") is None


class TestNormInquiryEscalationDetection:
    """規範照会・是正要求検出時の`additionalContext`注入とクールダウン判定の検証。"""

    def test_norm_inquiry_pattern_emits_additional_context(self, tmp_path: pathlib.Path):
        """規範照会パターン（`norm-inquiry`カテゴリ）でリマインダーが注入される。"""
        sid = "norm-inquiry-detect"
        _prime_counter(sid, tmp_path, turns=_COOLDOWN_TURNS - 1)
        result = _run(
            {"session_id": sid, "prompt": _NORM_INQUIRY_INPUTS["norm-inquiry"][0]},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        context = _additional_context(result)
        assert context is not None
        assert context.startswith("[auto-generated: agent-toolkit/user_prompt_submit]")
        assert context.endswith(
            "(Auto-generated hook notice; evaluate relevance against the conversation context before acting.)"
        )
        assert _read_state(tmp_path, sid).get("norm_inquiry_last_injected") == _COOLDOWN_TURNS

    def test_correction_request_pattern_emits_additional_context(self, tmp_path: pathlib.Path):
        """是正要求パターン（`correction-request`カテゴリ）でリマインダーが注入される。"""
        sid = "correction-request-detect"
        _prime_counter(sid, tmp_path, turns=_COOLDOWN_TURNS - 1)
        result = _run(
            {"session_id": sid, "prompt": _NORM_INQUIRY_INPUTS["correction-request"][0]},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert _additional_context(result) is not None

    def test_simple_question_without_norm_reference_emits_nothing(self, tmp_path: pathlib.Path):
        """規範・ルール言及を伴わない単純な質問・要望では追加出力が発生しない。"""
        sid = "simple-question"
        _prime_counter(sid, tmp_path, turns=_COOLDOWN_TURNS - 1)
        result = _run(
            {"session_id": sid, "prompt": _NORM_INQUIRY_INPUTS["none"][1]},
            state_dir=tmp_path,
        )
        assert result.returncode == 0
        assert _additional_context(result) is None

    def test_cooldown_suppresses_immediate_redetection(self, tmp_path: pathlib.Path):
        """クールダウン中（直近注入から`_COOLDOWN_TURNS`未満）の再検出は抑止される。"""
        sid = "cooldown-suppress"
        _prime_counter(sid, tmp_path, turns=_COOLDOWN_TURNS - 1)
        first = _run(
            {"session_id": sid, "prompt": _NORM_INQUIRY_INPUTS["norm-inquiry"][0]},
            state_dir=tmp_path,
        )
        assert _additional_context(first) is not None
        # 直後の再検出（カウンター差分1 < _COOLDOWN_TURNS）は抑止される。
        second = _run(
            {"session_id": sid, "prompt": _NORM_INQUIRY_INPUTS["correction-request"][0]},
            state_dir=tmp_path,
        )
        assert second.returncode == 0
        assert _additional_context(second) is None


class TestBoundaryConditions:
    """空文字列・単一行・末尾改行有無・複数行の境界条件を`main()`経由で検証する。"""

    def test_empty_string_prompt_exits_zero_without_output(self, tmp_path: pathlib.Path):
        sid = "boundary-empty-prompt"
        result = _run({"session_id": sid, "prompt": ""}, state_dir=tmp_path)
        assert result.returncode == 0
        assert _additional_context(result) is None
        assert _read_state(tmp_path, sid) == {}

    def test_single_line_without_trailing_newline(self, tmp_path: pathlib.Path):
        sid = "boundary-single-line"
        _prime_counter(sid, tmp_path, turns=_COOLDOWN_TURNS - 1)
        prompt = _NORM_INQUIRY_INPUTS["norm-inquiry"][0]
        assert not prompt.endswith("\n")
        result = _run({"session_id": sid, "prompt": prompt}, state_dir=tmp_path)
        assert result.returncode == 0
        assert _additional_context(result) is not None

    def test_single_line_with_trailing_newline(self, tmp_path: pathlib.Path):
        sid = "boundary-trailing-newline"
        _prime_counter(sid, tmp_path, turns=_COOLDOWN_TURNS - 1)
        prompt = _NORM_INQUIRY_INPUTS["norm-inquiry"][0] + "\n"
        result = _run({"session_id": sid, "prompt": prompt}, state_dir=tmp_path)
        assert result.returncode == 0
        assert _additional_context(result) is not None

    def test_multi_line_prompt_with_match_on_later_line(self, tmp_path: pathlib.Path):
        """先頭行が非該当でも、プロンプト全文の後続行に検出語があれば検出する。"""
        sid = "boundary-multiline"
        _prime_counter(sid, tmp_path, turns=_COOLDOWN_TURNS - 1)
        prompt = "背景を説明します。\n" + _NORM_INQUIRY_INPUTS["correction-request"][0] + "\n補足も書きます。"
        result = _run({"session_id": sid, "prompt": prompt}, state_dir=tmp_path)
        assert result.returncode == 0
        assert _additional_context(result) is not None
