# Hookの出力フィールド

本書はhookの出力フィールドの選択方針と、`PermissionRequest`・`UserPromptSubmit`の出力の扱いを定める。

## 出力フィールドの使い分け

本節は出力先の選択方針だけを定め、各フィールドのスキーマとイベント別の対応可否は`claude-hooks.md`「hookスクリプトの基本プロトコル」が挙げる公式ドキュメントに従う。

イベントごとの出力契約は`agent-toolkit/agent_toolkit/_testing/hook_output_contract.py`が公式のHooksリファレンスをJSON Schemaで写して定める。`agent-toolkit/agent_toolkit/_hooks/output_contract_test.py`が登録済みの全hookの出力がその契約に合うか確かめる。hookを追加または変更する場合は、契約とテストを同じ変更単位で更新する。

Claude Codeの`Stop hook error: JSON validation failed`はプロンプト型hookの評価器がモデルの応答をJSONとして解析できなかった場合だけに出る。`/goal`はセッション限りのプロンプト型Stop hookを登録するため、`/goal`を設定したセッションではコマンド型hookの出力形式と無関係にこの表示が現れる。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/writing-standards/references/claude-hooks-output.md：出力フィールドの使い分け：2026年9月4日」にある。

PreToolUse・PostToolUse・UserPromptSubmitでエージェントに行動を促す場合は`hookSpecificOutput.additionalContext`を第一の通知手段として使う。各フィールドがターン継続を強制するかは後掲の表に従う。
stderr出力は`exit 2`のblockと組み合わせる場合に限る。
`systemMessage`はユーザーの判断・操作に影響する情報通知に限る（失敗しない自動補正の発動など対応を要しない反復事象は対象外）。
Stop/SubagentStopでターン継続を強制する場合、エラーとしての遮断（振り返り誘導等）は`decision: "block"`＋`reason`、想定内の助言は`hookSpecificOutput.additionalContext`を使う。
永続ログは`_hooks.stop_session.append_stop_log`等の専用APIに集約する。

| フィールド | 表示先 | 用途 |
| --- | --- | --- |
| `hookSpecificOutput.additionalContext` | エージェント | 行動を促す主要な通知手段。PreToolUse・PostToolUse・UserPromptSubmitでは継続を強制せず、Stop/SubagentStopでは継続を強制する |
| `hookSpecificOutput.updatedToolOutput` | エージェント | PostToolUseでモデルへ渡すツール結果の置き換え。同じイベントの複数のhookは元の出力に並行して動き、最後に返った置き換えが採られる |
| `reason` | エージェント（`decision: "block"`時のみ） | blockを併用する場合の理由欄 |
| `permissionDecisionReason` | deny時はエージェント、allow/ask時はユーザーのみ | PreToolUseの決定理由 |
| `systemMessage`・`stopReason` | ユーザーのみ | 情報通知と`continue: false`時の終了メッセージ |
| `decision.*` | PermissionRequest専用 | 許可・拒否の決定。`hookSpecificOutput`直下に置く |

deny時の`permissionDecisionReason`と`hookSpecificOutput.additionalContext`はどちらもエージェントに届くため、片方に統一する（努力目標）。

`decision: "block"`の挙動はイベント別に異なる。
Stop/SubagentStopでは停止を防いでターン継続を強制し、PostToolUseではblock理由を直前のツール結果に添えて返す。
PreToolUse・PostToolUse・UserPromptSubmitで挙動の強制が不要であれば`additionalContext`単独で出力する。

block・warn・noticeの本文の整形（整形関数、`fix`の要否、種別、反復時の縮約と昇格）は`claude-hooks-messages.md`「通知本文の整形」に従う。

警告専用のPreToolUse出力は`hookSpecificOutput.additionalContext`だけを返し、`permissionDecision`を省略する（通常の権限フローが適用される）。

### PermissionRequest

確認ダイアログ表示時に発火し、ユーザーに代わって許可・拒否を決める。PreToolUseと異なり`hookSpecificOutput`直下に`decision`オブジェクトを置き、`hookEventName`は`"PermissionRequest"`とする。

組み込みのdeny / askルールはhookの戻り値に関わらず評価される。`.claude/`配下への書き込み確認等の組み込みaskルールはPreToolUseの`allow`では上書きできず、PermissionRequestの`decision.behavior: "allow"`でダイアログを抑制できる。組み込みdenyルールは`allow`を返しても適用される。
`updatedInput`は入力値を変えるだけで確認ダイアログは残るため、ダイアログを伴う値を拒否するにはブロックで扱う。

`Read(*.key)`のようにディレクトリを含まないdeny規則は、gitignore構文に従って任意の深さで一致するため、ディレクトリを走査する読み取りコマンドを一律に確認ダイアログの対象へ変える。
このダイアログは本イベントの`allow`でも抑止できないため、保護する対象はそのファイルを持つリポジトリの設定へ、走査対象を巻き込まない具体的なパスで書く。

`matcher`はツール名で評価する（`Bash` / `Edit|Write`等）。入力payloadは`tool_name` / `tool_input`に加え`permission_suggestions`配列を持つ。

### UserPromptSubmit

`hookSpecificOutput.additionalContext`をそのターンの応答生成前にユーザー発話へ前置注入する誘導に使い、受理する出力はそこまでとする。
Claude Codeでは`decision`と独立に注入でき、stdoutのプレーン出力もコンテキストへ加わる。Codexでは`hookSpecificOutput.hookEventName`を`UserPromptSubmit`として`additionalContext`を返す。

Claude CodeのUserPromptSubmit payloadは現在のセッション名を持たず、計画ファイルを扱うhookは、同一セッションでまだ出力していない場合だけ計画ファイル名のstemを`sessionTitle`へ一度出力する（Claude Code専用）。`additionalContext`も要る呼び出しでは、`hookSpecificOutput`へ両方を含む1つのJSONを返す。

応答契約を注入する注記は、本文の種別で頻度を分ける。

- エージェント向け文書の読込だけを求める注記は、基準文書の所在だけを示す1文とし、経過時間によらず通常発話ごとに返す（介入のたびに規範の所在を想起させ、文脈へ載る量を一定に保つ）
- 判定手順を本文へ持つ注記は、直前の通常発話からの経過時間が閾値以上の通常発話にだけ返す。初回の通常発話には返さないが、基準の時刻は初回から毎回更新する

対象の通常発話はユーザーが入力したスラッシュコマンドを含み、ハーネスが挿入した通知と機械注入のターンは対象外とする。成立した注記は1つの`additionalContext`へ結合し、Codex payloadでも同じく返す。

ユーザーが書いた文は、Claude Codeの`AskUserQuestion`の回答（提示した`label`と一致しない`answers`の値、`response`、`annotations`の`notes`）としてPostToolUseにも届く。発話の内容を判定材料とする注記は両hookで同じ定義の本文を使い、`AskUserQuestion`の自由記述へは、1回の質問につき1回しか生じないため閾値を適用せず毎回返す。
