"""Stop判定の1モジュールを単独で実行し、その判定だけを返すhook応答を標準出力へ書くテスト用の起動スクリプト。

本番のStopは`agent_toolkit._hooks.stop`が全ての判定を集約して1つの応答にするため、1つの判定の結果だけを
確かめるテストは集約の影響（他の判定の遮断や連続blockの計数）を受ける。本スクリプトは第1引数で指定した
判定モジュールの`evaluate`だけを呼び、結果をStopの応答の形へ写す。終了の許可は何も書かない。
環境変数と一時ディレクトリをテストごとに分けるため、`fork_runner.run_script`で別プロセスとして起動する。
"""

import importlib
import json
import sys


def response_for(decision: str, body: str) -> dict[str, object] | None:
    """判定1件の結果を、その判定だけを含むStopの応答へ写す。終了の許可は`None`を返す。"""
    if decision == "block":
        return {"decision": "block", "reason": body}
    if decision == "notify":
        return {"hookSpecificOutput": {"hookEventName": "Stop", "additionalContext": body}}
    if decision == "notify_user":
        return {"systemMessage": body}
    return None


def main(argv: list[str]) -> int:
    """`argv[0]`の判定モジュールへ標準入力のStop入力を渡し、応答を標準出力へ書く。"""
    module = importlib.import_module(f"agent_toolkit._hooks.{argv[0]}")
    decision, body = module.evaluate(sys.stdin.read())
    response = response_for(decision, body)
    if response is not None:
        print(json.dumps(response, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
