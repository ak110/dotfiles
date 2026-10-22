# Hookの出力フィールド

本書はhookの出力フィールドの選択方針と、`PermissionRequest`・`UserPromptSubmit`の出力の扱いを定める。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `claude-hooks.md#「hookスクリプトの基本プロトコル」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/claude-hooks.md#「hookスクリプトの基本プロトコル」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `claude-hooks-messages.md#「通知本文の整形」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/claude-hooks-messages.md#「通知本文の整形」` |

## 出力フィールドの使い分け

本節は出力先の選択方針だけを定め、各フィールドのスキーマとイベント別の対応可否は`claude-hooks.md`「hookスクリプトの基本プロトコル」が挙げる公式ドキュメントに従う。

イベントごとの出力契約を公式リファレンスからスキーマへ写し、登録したhookの応答を検証する。hook追加・変更時は契約と検証を同じ変更単位で更新する。

Claude Codeの`Stop hook error: JSON validation failed`はプロンプト型hookの評価器がモデルの応答をJSONとして解析できなかった場合だけに出る。`/goal`はセッション限りのプロンプト型Stop hookを登録するため、`/goal`を設定したセッションではコマンド型hookの出力形式と無関係にこの表示が現れる。
監査記録は`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/claude-hooks-output.md：出力フィールドの使い分け：2026年9月4日」にある。

PreToolUse・PostToolUse・UserPromptSubmitでエージェントに行動を促す場合は`hookSpecificOutput.additionalContext`を第一の通知手段として使う。各フィールドがターン継続を強制するかは後掲の表に従う。
stderr出力は`exit 2`のblockと組み合わせる場合に限る。
`systemMessage`はユーザーの判断・操作に影響する情報通知に限る（失敗しない自動補正の発動など対応を要しない反復事象は対象外）。
Stop/SubagentStopでターン継続を強制する場合、エラーとしての遮断（振り返り誘導等）は`decision: "block"`＋`reason`、想定内の助言は`hookSpecificOutput.additionalContext`を使う。

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

### 出力上限

[公式HooksリファレンスのJSON output](https://code.claude.com/docs/en/hooks#json-output)は`additionalContext`・`systemMessage`・`initialUserMessage`の各文字列と、通常の標準出力に10,000文字の上限を定める。
JSONはフィールドの値ごと、通常の標準出力は全体を測り、同じイベントで動く複数hookの値も個別に測る。
超過するとClaude Codeは全文をセッション内のファイルへ保存し、ファイルパスと最大2,000文字の冒頭プレビューへ置き換える。
Claude Code自身はモデルにファイルの読取を求めないため、必ず届く必要がある情報は上限内に収める。

公式資料の10,000文字制限は、PreToolUseが終了コード2で返す標準エラーを対象に含めていない。
標準エラー44,224文字が、接頭辞込み44,346文字のツール結果として全文届いた観測がある。
この値は一条件の配送量であり、標準エラーの上限を保証する値ではない。
監査記録は`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/claude-hooks-output.md：出力上限：2026年10月20日」にある。

### PermissionRequest

確認ダイアログ表示時に発火し、ユーザーに代わって許可・拒否を決める。PreToolUseと異なり`hookSpecificOutput`直下に`decision`オブジェクトを置き、`hookEventName`は`"PermissionRequest"`とする。

組み込みのdeny / askルールはhookの戻り値に関わらず評価される。`.claude/`配下への書き込み確認等の組み込みaskルールはPreToolUseの`allow`では上書きできず、PermissionRequestの`decision.behavior: "allow"`でダイアログを抑制できる。組み込みdenyルールは`allow`を返しても適用される。
`updatedInput`は入力値を変えるだけで確認ダイアログは残るため、ダイアログを伴う値を拒否するにはブロックで扱う。

`Read`のdeny規則でディレクトリ指定を欠くglobは、gitignore構文に従って任意の深さで一致するため、ディレクトリを走査する読み取りコマンドを一律に確認ダイアログの対象へ変える。
このダイアログは本イベントの`allow`でも抑止できないため、保護する対象はそのファイルを持つリポジトリの設定へ、走査対象を巻き込まない具体的なパスで書く。

`matcher`はツール名で評価する（`Bash` / `Edit|Write`等）。入力payloadは`tool_name` / `tool_input`に加え`permission_suggestions`配列を持つ。

### UserPromptSubmit

`hookSpecificOutput.additionalContext`をそのターンの応答生成前にユーザー発話へ前置注入する誘導に使う。
Claude Codeでは`decision`と独立に注入でき、stdoutのプレーン出力もコンテキストへ加わる。Codexでは`hookSpecificOutput.hookEventName`を`UserPromptSubmit`として`additionalContext`を返す。

## 組織向け設定

Claude Code 2.1.304以降の`cc-plugin-sec-default`が有効な管理設定・Team・Enterpriseの環境では、ユーザーが入れたプラグインの一部のFunction hooksの実行が省かれる。
全配布先で必要な処理は、`hooks.json`のコマンドhookか、実行が省かれないFunction hooksのイベントへ置く。
実行が省かれる対象は次のとおりで、これらへのFunction hooksの登録を避ける。

- `classic.`で始まるイベント
- `prompt.section`・`prompt.context`・`prompt.attachment`・`prompt.compose`・`skill.prompt`・`attribution.text`・`session.compact`・`settings.read`
- doorが`attachment`・`hook-context`・`compaction`の`session.append`

監査記録は`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/claude-hooks-output.md：組織向け設定：2026年10月20日」にある。
