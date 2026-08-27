r"""Claude Code Stopフック: 応答終了で入力待ちになったときに端末ベルを鳴らす。

`Notification`の入力待ち系種別のうち`idle_prompt`は応答終了の約60秒後に発火するため、
背景のサブエージェント・コマンドの完了を待ってターンを終えた場合も同じ扱いで発火し、
ユーザーの入力を要さない待機でベルが鳴っていた。そのため`idle_prompt`をベルの対象から外し、
代わりに本hookがStopイベントで「ユーザーの入力を待つ状態になった場合だけ」ベルを鳴らす。

判定順序は以下のとおり。

1. ペイロード解析失敗または`session_id`欠落: 判定材料が無いためベルを鳴らさない
2. 他のStop系hookがターン継続をblockした後の再呼び出し（`stop_hook_active`が真）:
   応答は終了しておらずベルを鳴らさない
3. 常駐ループから起動された自律セッション: ユーザーの入力を待たないためベルを鳴らさない
4. 背景のサブエージェント・コマンド等が未完了: 待機の継続でありベルを鳴らさない
5. 上記いずれでもない: ベルを鳴らす

ベルはフック出力JSONの`terminalSequence`フィールドで返し、Claude Code自身の端末書き込み経路で
送出させる。フックは制御端末のない独立セッションで実行され`/dev/tty`を開けないためである。

他のStop系hookの判定（`agent-toolkit`の振り返り誘導、`agent-toolkit/scripts/autonomous_exit.py`の
終了工程の再促）とは独立に動くため、同一Stopサイクルの1回目では当該hookのblockに先行して
ベルが鳴り得る。block後の再呼び出しは`stop_hook_active`により鳴らさない。
"""

import json
import os
import pathlib
import sys

# agent-toolkit の共通ゲートモジュールを import する。
# plugin が無効化されていても dotfiles リポジトリ上にファイルが存在し続けるため import は成立する。
sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parent.parent / "agent-toolkit" / "scripts"),
)
from _stop_gate import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    append_stop_log,
    is_pending_async_work,
)

# pylint: disable-next=wrong-import-position,import-error
from _stop_gate import parse_stop_session as _parse_stop_session  # noqa: E402

# 常駐ループから起動されたセッションであることを示す環境変数名。
_ENV_PROCESS_LOOP = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION"

# 更新中に旧process-loopと併存するため受理する移行互換名。
_LEGACY_ENV_PROCESS_LOOP = "DOTFILES_AUTONOMOUS_EXIT_REQUIRED"

# 端末ベル（BEL）の制御文字。
_BELL = "\a"


def _approve() -> None:
    """ベルを鳴らさず空応答で終端する。共通入口の例外フォールバックも本関数を呼ぶ。"""
    print(json.dumps({}, ensure_ascii=False))


def _ring() -> None:
    """Claude Codeの端末書き込み経路でベルを送出させる。"""
    print(json.dumps({"terminalSequence": _BELL}, ensure_ascii=False))


def main(payload_text: str) -> int:
    """入力待ちになった場合だけベルを鳴らすエントリポイント。"""
    resolved = _parse_stop_session(payload_text, _approve)
    if resolved is None:
        return 0
    session_id, payload = resolved

    # 他のStop hookがターン継続をblockした後の再呼び出し。応答は終了しておらず
    # ユーザーの入力待ちでもないため、判定処理を行わずベルを鳴らさない。
    if payload.get("stop_hook_active") is True:
        append_stop_log(session_id, "silent_stop_hook_active", {"stop_hook_active": True})
        _approve()
        return 0

    if os.environ.get(_ENV_PROCESS_LOOP) == "1" or os.environ.get(_LEGACY_ENV_PROCESS_LOOP) == "1":
        append_stop_log(session_id, "silent_autonomous_session", {})
        _approve()
        return 0

    raw_transcript = payload.get("transcript_path", "")
    transcript_path = raw_transcript if isinstance(raw_transcript, str) else ""
    if transcript_path and is_pending_async_work(transcript_path, session_id):
        append_stop_log(session_id, "silent_pending_async", {})
        _approve()
        return 0

    append_stop_log(session_id, "ring_bell", {})
    _ring()
    return 0
