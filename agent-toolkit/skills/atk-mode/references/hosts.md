# ホストごとの差

agent-toolkitを配布する4つのホストで、手順書が分岐に使う差を1つの表にまとめる。
各セルは値と詳細の所在だけを持ち、操作の手順は所在の手順書が定める。
手順書がClaude Codeのツール名で書いた操作は、各ホストで同じ結果になる操作へ読み替える（Codexは`agent-toolkit:atk-mode/playbooks/host-codex.md`「ツール名の読み替え」）。

## 差の一覧

| 項目 | Claude Code | Codex | Antigravity | OpenCode |
| --- | --- | --- | --- | --- |
| `atk-mode`の届き方 | SessionStartの1行の通知でスキル`agent-toolkit:atk-mode`を起動する | SessionStartの1行の通知で`<plugin root>/skills/atk-mode/SKILL.md`を全文読む（`agent-toolkit:atk-mode/playbooks/host-codex.md`「Claude Code向けに配置された規範の読込」） | SessionStartの1行の通知でスキルを起動する。長い`SKILL.md`は本文を各スキルの`references/antigravity-full-instructions.md`へ分けて配置する | 設定の`instructions`へ登録した数行の案内でスキル`agent-toolkit-atk-mode`を読む |
| 会話の圧縮の後 | SessionStart（`compact`）の通知で`atk-mode`を起動し直す（`agent-toolkit:atk-mode/playbooks/host-claude-code.md`） | SessionStart（`compact`）の通知で`atk-mode`を読み直す | 未確認 | `instructions`の案内が圧縮の後の読み直しを求める |
| `agents_server`の委譲先への届き方 | 起動の本文（`agent-toolkit:atk-mode/playbooks/subagent.md`と役割文書） | 同左 | 同左 | 同左 |
| 組み込みの委譲 | `Agent`ツール。SubagentStartの通知で`agent-toolkit:atk-mode/playbooks/subagent.md`を読む（`agent-toolkit:atk-mode/playbooks/host-claude-code.md`） | `spawn_agent`（`agent-toolkit:atk-mode/playbooks/host-codex.md`「ツール名の読み替え」） | 生成した`agents/<役割名>.md`が役割文書を読ませる | agent-toolkitは定義を配らない |
| `agents_server`のengine名 | `claude` | `codex` | `agy` | `opencode`（`orchestrate_model`には指定できない） |
| ユーザーへの送達 | `send_to_user`を使える時は常に`send_to_user`、それ以外は通常の応答本文（`agent-toolkit:atk-mode/playbooks/host-claude-code.md`「ツールの入出力」） | `commentary`と`final`（`agent-toolkit:atk-mode/playbooks/host-codex.md`「ユーザー確認と終了」） | 通常の応答本文 | 通常の応答本文 |
| 構造化質問 | `AskUserQuestion`（`agent-toolkit:atk-mode/playbooks/host-claude-code.md`） | `request_user_input`（Plan mode）、`request_user_input_async`（`agent-toolkit:atk-mode/playbooks/host-codex.md`「ユーザー確認と終了」） | `agent-toolkit:atk-mode/references/choice-construction.md`「質問の形式」の固定形式 | 同左 |
| バックグラウンド実行と待機 | `Bash`のバックグラウンド実行と完了通知、`atk agents wait`（`agent-toolkit:atk-mode/playbooks/host-claude-code.md`「待機中の定期再確認と背景転換」） | `functions.exec`と`functions.wait`・`write_stdin`の二層（`agent-toolkit:atk-mode/playbooks/host-codex.md`「agents_serverの二層待機」） | `atk agents wait`のフォアグラウンド回収 | 同左 |
| MCPツールの呼び方 | ツールとして直接呼ぶ | `functions.exec`の中から`tools.mcp__<サーバー名>__<ツール名>(...)`で呼ぶ（`agent-toolkit:atk-mode/playbooks/host-codex.md`「Codex固有の入出力」） | ツールとして直接呼ぶ（`agents_server`と`pyfltr`だけを登録） | 登録しない |
| hook | 有り | 有り（定義の変更後は`/hooks`で信頼し直すまで動かない） | 有り（6イベント） | 無し |
| 自身の会話の記録 | SessionStartが通知するtranscriptの絶対パス（`agent-toolkit:atk-mode/references/session-records.md`） | `CODEX_THREAD_ID`（同） | `agents_server`が起動した会話だけ`atk agents logs <session_id>`（同） | `ses_`で始まるセッションID（同） |

## 確かめていない範囲

AntigravityとOpenCodeの、ユーザーへの送達、構造化質問、バックグラウンド実行の値は、会話内modが無いこととhook・MCPの登録の有無から導いた推定であり、実機では確かめていない。
Antigravityの圧縮の後の通知も未確認である。
これらのホストで手順書の分岐が要る時は、実際のツールの一覧と応答で確かめてから進める。
