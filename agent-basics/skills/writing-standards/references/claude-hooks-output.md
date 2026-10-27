# Hookの出力フィールド

本書では、hookが出力するフィールドの選択方針と、`PermissionRequest`および`UserPromptSubmit`における出力の扱いを定める。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `claude-hooks.md#「hookスクリプトの基本プロトコル」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/claude-hooks.md#「hookスクリプトの基本プロトコル」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `claude-hooks-messages.md#「通知本文の整形」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/claude-hooks-messages.md#「通知本文の整形」` |

## 出力フィールドの使い分け

本節では出力先の選択方針を定める。各フィールドのスキーマやイベントごとの対応状況は、`claude-hooks.md`「hookスクリプトの基本プロトコル」で挙げる公式ドキュメントに従う。

イベントごとの出力契約は公式リファレンスからスキーマに反映し、登録したhookの応答を検証する。hookの追加や変更をする際は、出力契約と検証処理を同じ変更単位で更新する。

Claude Codeの`Stop hook error: JSON validation failed`は、プロンプト型hookの評価器がモデルの応答をJSONとして解析できなかった場合に発生する。`/goal`コマンドはセッション中のみ有効なプロンプト型Stop hookを登録するため、`/goal`を設定したセッションでは、コマンド型hookの出力形式にかかわらずこの表示が現れることがある。
監査記録は`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/claude-hooks-output.md：出力フィールドの使い分け：2026年9月4日」にある。

PreToolUse、PostToolUse、UserPromptSubmitでエージェントに行動を促す際は、`hookSpecificOutput.additionalContext`を第一の通知手段として用いる。各フィールドがターン継続を強制するかどうかは、以下の表に従う。
標準エラー出力（stderr）は、終了コード2による遮断（block）と組み合わせる場合に限って使用する。
`systemMessage`は、ユーザーの判断や操作に影響する重要な情報通知に限定する（失敗しない自動補正の発動など、ユーザーの対応を必要としない定常的な事象には使わない）。
StopやSubagentStopでターン継続を強制する場合、振り返りの誘導といったエラー相当の遮断には`decision: "block"`と`reason`を使い、想定内の助言には`hookSpecificOutput.additionalContext`を使う。

| フィールド | 表示先 | 用途 |
| --- | --- | --- |
| `hookSpecificOutput.additionalContext` | エージェント | 行動を促す主要な通知手段。PreToolUse・PostToolUse・UserPromptSubmitでは継続を強制せず、Stop・SubagentStopでは継続を強制する |
| `hookSpecificOutput.updatedToolOutput` | エージェント | PostToolUseでモデルへ渡すツール結果の置き換え。同じイベントの複数のhookは元の出力に対して並行して動作し、最後に返された置き換え結果が採用される |
| `reason` | エージェント（`decision: "block"`時のみ） | blockと併用する場合の理由欄 |
| `permissionDecisionReason` | deny時はエージェント、allow/ask時はユーザーのみ | PreToolUseの決定理由 |
| `systemMessage`・`stopReason` | ユーザーのみ | 情報通知および`continue: false`時の終了メッセージ |
| `decision.*` | PermissionRequest専用 | 許可や拒否の決定。`hookSpecificOutput`の直下に配置する |

deny時の`permissionDecisionReason`と`hookSpecificOutput.additionalContext`はどちらもエージェントに届くため、片方に統一する（努力目標）。

`decision: "block"`の動作はイベントごとに異なる。
StopやSubagentStopではセッション停止を防いでターン継続を強制し、PostToolUseでは直前のツール結果にblock理由を添えて返す。
PreToolUse、PostToolUse、UserPromptSubmitで挙動の強制が不要な場合は、`additionalContext`単独で出力する。

block、warn、noticeの各通知本文の整形（整形関数の利用、`fix`の要否、種別、繰り返した際の要約や重要度引き上げ）は、`claude-hooks-messages.md`「通知本文の整形」に従う。

警告専用のPreToolUse出力では`hookSpecificOutput.additionalContext`のみを返し、`permissionDecision`は省略する（通常の権限判定フローが適用される）。

### 出力上限

[公式HooksリファレンスのJSON output](https://code.claude.com/docs/en/hooks#json-output)では、`additionalContext`、`systemMessage`、`initialUserMessage`の各文字列、および通常の標準出力に10,000文字の上限が定められている。
文字数は、JSONでは各フィールドの値ごとに、通常の標準出力では全体でカウントされ、同一イベントで動く複数のhookも個別にカウントされる。
上限を超過した場合、Claude Codeは全文をセッション内の一時ファイルに保存し、ファイルパスと先頭最大2,000文字のプレビュー表示に置き換える。
Claude Code自身がモデルにそのファイルの読み取りを促すことはないため、確実に届ける必要がある情報は10,000文字の上限内に収める。

公式資料の10,000文字制限には、PreToolUseが終了コード2で返す標準エラー出力は含まれていない。
標準エラー出力44,224文字が、接頭辞を含めて44,346文字のツール結果として全文届いた観測記録がある。
ただし、この数値は特定の条件下で観測された配送量であり、標準エラー出力の上限を恒久的に保証するものではない。
監査記録は`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/claude-hooks-output.md：出力上限：2026年10月20日」にある。

### PermissionRequest

確認ダイアログの表示時に発火し、ユーザーに代わってツールの実行許可や拒否を判定する。PreToolUseとは異なり、`hookSpecificOutput`の直下に`decision`オブジェクトを配置し、`hookEventName`には`"PermissionRequest"`を指定する。

組み込みのdenyルールやaskルールは、hookの戻り値にかかわらず評価される。たとえば`.claude/`配下への書き込み確認といった組み込みのaskルールは、PreToolUseで`allow`を返しても上書きできないが、PermissionRequestで`decision.behavior: "allow"`を返すことでダイアログの表示を抑制できる。なお、組み込みのdenyルールは`allow`を返しても強制的に適用される。
`updatedInput`は入力値を変更するだけで確認ダイアログ自体は残るため、確認ダイアログが表示されるような入力を拒否したい場合はブロックとして処理する。

`Read`ツールのdeny規則でディレクトリの指定がないglobパターンを使うと、gitignore構文に従って任意の深さで一致するため、ディレクトリを走査する読み取りコマンドが一律で確認ダイアログの対象になる。
このダイアログはPermissionRequestの`allow`でも抑制できないため、保護対象のファイルは走査対象全体を巻き込まないように具体的なパスでリポジトリの設定に記述する。

`matcher`はツール名（`Bash`、`Edit|Write`など）で評価する。入力payloadには`tool_name`や`tool_input`に加えて`permission_suggestions`配列が含まれる。

### UserPromptSubmit

エージェントがそのターンの応答を生成する前に、`hookSpecificOutput.additionalContext`を用いてユーザー発話の先頭にコンテキストを前置注入する。
Claude Codeでは`decision`とは独立して注入でき、標準出力（stdout）へのテキスト出力もコンテキストに追加される。Codexでは`hookSpecificOutput.hookEventName`に`UserPromptSubmit`を指定して`additionalContext`を返す。

## 組織向け設定

Claude Code 2.1.304以降で`cc-plugin-sec-default`が有効になっている管理環境（Team・Enterpriseなど）では、ユーザーが導入したプラグインに含まれる一部のFunction hooksの実行が省略される。
すべての配布先で確実に実行する必要がある処理は、`hooks.json`のコマンドhookか、実行が省略されないFunction hooksイベントに配置する。
実行が省略される対象は以下のとおりであり、これらにFunction hooksを登録することは避ける。

- `classic.`で始まるイベント
- `prompt.section`、`prompt.context`、`prompt.attachment`、`prompt.compose`、`skill.prompt`、`attribution.text`、`session.compact`、`settings.read`
- doorが`attachment`、`hook-context`、`compaction`である`session.append`

監査記録は`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/claude-hooks-output.md：組織向け設定：2026年10月20日」にある。
