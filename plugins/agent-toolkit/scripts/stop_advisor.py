#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Claude Code plugin agent-toolkit: Stop hook.

Claude Code が停止しようとするタイミングで発火する。

未コミット変更がある場合はセッション終了を block する。
ただし Claude がユーザーへ質問中（AskUserQuestion ツール使用、またはテキストに
? / ？ が含まれる）の場合は block せず approve する（質問への割り込みを防ぐため）。

transcript を分析してユーザーからの修正指示の多寡を判定し、
閾値を超えた場合に CLAUDE.md 更新を提案する。
codex exec resume が多い場合も同様に提案する。

1 セッションにつき 1 回のみ発火する。
2 回目以降の Stop は即座に approve する。

exit code: 常に 0。
stdout に JSON (decision: approve | block) を出力する。
"""

import contextlib
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import traceback

# --- 修正キーワード ---

_CORRECTION_KEYWORDS: tuple[str, ...] = (
    "違う",
    "そうじゃ",
    "そうでなく",
    "じゃなく",
    "間違",
    "やり直",
    "ではなく",
    "戻して",
    "さっき言った",
    "指示した通り",
    "指示通り",
)

# 低すぎると通常会話（「違う話だけど」等）で誤検知する。
# 3 以上なら意図的な修正指示の繰り返しと見なせる経験的な閾値。
_KEYWORD_THRESHOLD = 3
# resume 1 回は正常フロー（初回不合格→修正→合格）のため、
# 2 以上を異常な繰り返しと判定する。
_CODEX_RESUME_THRESHOLD = 2

# LLM 宛てメッセージの共通プレフィックス / サフィックス。
# 詳細は skills/claude-meta-rules/references/claude-hooks.md を参照。
_MESSAGE_PREFIX = "[auto-generated: agent-toolkit/stop_advisor]"
_MESSAGE_SUFFIX = "(Auto-generated hook notice; evaluate relevance against the conversation context before acting.)"


def _llm_notice(body: str, *, tag: str = "") -> str:
    """LLM 宛てメッセージを標準プレフィックス / サフィックス付きで整形する。"""
    prefix = f"{_MESSAGE_PREFIX}[{tag}]" if tag else _MESSAGE_PREFIX
    return f"{prefix} {body} {_MESSAGE_SUFFIX}"


# Claude Code のハーネスが user turn 内に注入するタグ。
# ユーザー発話ではないため修正キーワード集計の対象外とする。
_INJECTED_TAG_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL),
    re.compile(r"<user-prompt-submit-hook>.*?</user-prompt-submit-hook>", re.DOTALL),
    re.compile(r"<local-command-stdout>.*?</local-command-stdout>", re.DOTALL),
    re.compile(r"<local-command-caveat>.*?</local-command-caveat>", re.DOTALL),
)


def _state_path(session_id: str) -> pathlib.Path:
    """posttooluse.py と共通のパス規則。

    tempdir を使う理由: セッション状態は揮発で構わず、
    OS 再起動時に自動消去されるため永続化の必要がない。
    """
    return pathlib.Path(tempfile.gettempdir()) / f"claude-agent-toolkit-{session_id}.json"


def _read_state(path: pathlib.Path) -> dict:
    """状態ファイルを読み込む。

    ファイル未作成・破損時は空 dict を返し、初回セッションとして扱う。
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _write_state(path: pathlib.Path, state: dict) -> None:
    """状態ファイルを書き込む。

    書き込み失敗は無視する（hook の失敗でセッション停止を妨げない）。
    """
    with contextlib.suppress(OSError):
        path.write_text(json.dumps(state), encoding="utf-8")


def _extract_user_text(line: str) -> str | None:
    """Transcript の JSONL 1 行から user turn のテキストを抽出する。

    `type == "user"` かつ `isSidechain` でないエントリの message.content を読む。
    tool_result ブロックはツール出力でユーザー発話ではないため除外する。
    ハーネスが注入する system-reminder 等のタグも除去する
    (読み込まれる CLAUDE.md / ルールファイル本文がキーワードを含み false positive の原因になるため)。
    """
    try:
        entry = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if entry.get("type") != "user" or entry.get("isSidechain"):
        return None
    message = entry.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                value = block.get("text")
                if isinstance(value, str):
                    parts.append(value)
        text = "\n".join(parts)
    else:
        return None
    for pattern in _INJECTED_TAG_PATTERNS:
        text = pattern.sub("", text)
    return text


def _count_keywords(transcript_path: str) -> int:
    """Transcript 内のユーザー発話に含まれる修正キーワードの出現数を返す。"""
    try:
        lines = pathlib.Path(transcript_path).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return 0
    count = 0
    for line in lines:
        text = _extract_user_text(line)
        if text is None:
            continue
        for keyword in _CORRECTION_KEYWORDS:
            count += text.count(keyword)
    return count


def _is_assistant_asking_question(transcript_path: str) -> bool:
    """直前のアシスタントターンがユーザーへの質問を含んでいるかを確認する。

    以下のいずれかが成立する場合 True を返す。
    - AskUserQuestion ツール呼び出しが含まれている
    - テキストに ? または ？ が含まれている（位置は問わない）

    末尾判定にしない理由: アシスタントが質問文の後に補足・締めの文を書くケース
    （例:「…どうしますか？ お手数ですがご確認ください。」）で末尾に `?` が来ず、
    false positive でコミットを強行する挙動を避けるため。

    同一 message.id を持つ複数エントリ（テキストとツール呼び出しが別エントリに分割
    される場合がある）は 1 ターンとして扱い、テキストのないエントリが末尾に来る
    競合状態（hook 発火時点で transcript が未 flush）に対処する。

    未コミット変更ブロックの false positive を防ぐための判定。
    transcript 読み取りに失敗した場合は False を返す（安全側）。
    """
    try:
        lines = pathlib.Path(transcript_path).read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return False
    first_msg_id: str | None = None
    checked_count = 0
    saw_non_assistant = False  # 最初のアシスタントエントリ発見後に非アシスタントエントリが出たか
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if entry.get("type") != "assistant" or entry.get("isSidechain"):
            if first_msg_id is not None:
                # 別エントリが間に挟まった → 同一ターンの探索終了
                saw_non_assistant = True
            continue
        if saw_non_assistant:
            # 直前のアシスタントエントリとの間に別エントリが挟まっている → 別ターン
            return False
        message = entry.get("message")
        if not isinstance(message, dict):
            return False
        msg_id = message.get("id", "")
        if first_msg_id is None:
            first_msg_id = msg_id
        elif msg_id and first_msg_id and msg_id != first_msg_id:
            # message.id が両方設定されており異なる → 別ターン
            return False
        checked_count += 1
        if checked_count > 3:
            return False
        content = message.get("content")
        if not isinstance(content, list):
            return False
        texts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "AskUserQuestion":
                return True
            if block.get("type") == "text":
                text = block.get("text", "")
                if isinstance(text, str) and text.strip():
                    texts.append(text)
        if texts:
            joined = "\n".join(texts)
            return "?" in joined or "？" in joined
        # テキストなしエントリ → 同一ターンの前のエントリを確認する（ループ継続）
    return False


def _has_uncommitted_changes(cwd: str) -> bool:
    """作業ディレクトリに未コミットの変更があるか判定する。

    untracked ファイル (??) は対象外とする（意図的に未追跡の場合があるため）。
    git 未導入・リポジトリ外・コマンド失敗時は False を返す（安全側）。
    """
    if not cwd:
        return False
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
            cwd=cwd,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return any(line and not line.startswith("??") for line in result.stdout.splitlines())


def _git_status_for_display(cwd: str) -> str | None:
    """ユーザー表示用のgit status --shortを返す。

    未コミット変更がない場合・untrackedのみの場合・エラー時はNoneを返す。
    """
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True,
            text=True,
            check=False,
            cwd=cwd,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    if not output:
        return None
    # untrackedファイルのみの場合は表示しない
    if all(line.startswith("??") for line in output.splitlines()):
        return None
    return output


def _approve(cwd: str = "") -> None:
    output: dict[str, str] = {"decision": "approve"}
    if cwd:
        status = _git_status_for_display(cwd)
        if status:
            output["systemMessage"] = f"[git status]\n{status}"
    print(json.dumps(output))


def _block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}))


def _main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        _approve()
        return 0

    session_id = payload.get("session_id", "")
    if not isinstance(session_id, str) or not session_id:
        _approve()
        return 0

    state_file = _state_path(session_id)
    state = _read_state(state_file)

    # Stop のたびに git_log_checked をリセットする。
    # ユーザーが裏で push している可能性があるため、
    # 再開後の amend / rebase には改めて log 確認を要求する。
    if state.get("git_log_checked", False):
        state["git_log_checked"] = False
        _write_state(state_file, state)

    cwd = payload.get("cwd", "")
    transcript_path = payload.get("transcript_path", "")

    # --- 未コミット変更ブロック ---
    # Claude がユーザーに質問中の場合はスキップする（質問への割り込みを防ぐ）。
    # ブロックは 1 回に限る。理由:
    #   - Claude Code は block を受けると再度 Stop を試みるため、
    #     同一メッセージの繰り返しに意味がない
    #   - 2 回目以降は block_count != 0 でブロックせず、
    #     後続の stop_advice_given チェックへフォールスルーする
    #   - _is_assistant_asking_question の transcript flush タイミング問題
    #     （質問検出失敗）も 2 回目で自然に通過する
    if isinstance(cwd, str) and _has_uncommitted_changes(cwd):
        asking = isinstance(transcript_path, str) and _is_assistant_asking_question(transcript_path)
        if not asking:
            block_count = state.get("uncommitted_block_count", 0)
            if block_count == 0:
                state["uncommitted_block_count"] = block_count + 1
                _write_state(state_file, state)
                _block(
                    _llm_notice(
                        "uncommitted changes detected."
                        " Ask the user whether to commit the changes, or explain"
                        " why they should not be committed."
                        " Do not commit without user confirmation."
                    )
                )
                return 0
            # 2 回目以降: ブロックせず後続処理へフォールスルー

    # --- CLAUDE.md 更新提案 ---
    # セッション 1 回制限: block 後に Claude が再度 Stop するため、
    # 2 回目以降は即座に approve する。
    if state.get("stop_advice_given", False):
        _approve(cwd=cwd)
        return 0

    # transcript の修正キーワードを集計
    keyword_count = 0
    if isinstance(transcript_path, str) and transcript_path:
        keyword_count = _count_keywords(transcript_path)

    # codex resume の回数を取得
    codex_resume_count = state.get("codex_resume_count", 0)

    keyword_triggered = keyword_count >= _KEYWORD_THRESHOLD
    codex_triggered = codex_resume_count >= _CODEX_RESUME_THRESHOLD

    if not keyword_triggered and not codex_triggered:
        _approve(cwd=cwd)
        return 0

    # 発火: block 前に stop_advice_given を記録する。
    # block 後の再 Stop 時に上の早期リターンで即 approve するため。
    state["stop_advice_given"] = True
    _write_state(state_file, state)

    # 理由に応じたメッセージ構築
    parts: list[str] = ["session review:"]
    if keyword_triggered:
        parts.append(f" transcript analysis: {keyword_count} correction indicators detected.")
    if codex_triggered:
        parts.append(f" codex review iterations: {codex_resume_count} resume calls detected.")
    parts.append(
        " Before ending this session, please:"
        " (1) review whether agent.md procedures"
        " (bug-fix 3-step, verify-then-commit) were followed"
        " (2) consider updating CLAUDE.md with lessons learned"
        " (run /claude-md-management:revise-claude-md if appropriate)"
    )

    _block(_llm_notice("".join(parts)))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(_main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        _approve()
        sys.exit(0)
