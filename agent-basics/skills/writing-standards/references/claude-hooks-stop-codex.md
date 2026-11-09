# Stop/SubagentStopフック：Codexの差分

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 遮断と警告を選ぶ前 | `claude-hooks-block-warn-behavior.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-block-warn-behavior.md` |

## Codexの差分

CodexのStopフックでは、`decision: "block"`と`reason`を返すことで同一ターンを継続させる。
許可する場合は空のJSONオブジェクトを返す（Codexでは`hookSpecificOutput`が受理されないためである）。
非遮断の通知を送れない環境では、手順の支援案内を関連する操作の通知やドキュメント側に配置する。
通知を届けたいという理由だけで遮断（block）へ切り替えることは避ける。
Codex固有の入力には`model`がある。
Codex rolloutにおけるトランスクリプト形式は安定した仕様ではないため、完了判定やバックグラウンドタスクの判定入力には直接使わず、状態が欠落した際の復旧判定などで識別子の有無を確認する用途に限ってランタイム別に変換して扱う。
