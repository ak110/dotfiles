# Hookの出力フィールド

本書では、hookが出力するフィールドの選択方針と、`PermissionRequest`および`UserPromptSubmit`における出力の扱いを定める。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 出力フィールドの使い分け

本節では出力先の選択方針を定める。
各フィールドのスキーマやイベントごとの対応状況は、`claude-hooks.md`「hookスクリプトの基本プロトコル」で挙げる公式ドキュメントに従う。

イベントごとの出力契約は公式リファレンスからスキーマに反映し、登録したhookの応答を検証する。
hookの追加や変更をする際は、出力契約と検証処理を同じ変更単位で更新する。

Claude Codeの`Stop hook error: JSON validation failed`は、プロンプト型hookの評価器がモデルの応答をJSONとして解析できなかった場合に発生する。
`/goal`コマンドはセッション中のみ有効なプロンプト型Stop hookを登録するため、`/goal`を設定したセッションでは、コマンド型hookの出力形式にかかわらずこの表示が現れることがある。
監査記録は`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/claude-hooks-output.md：出力フィールドの使い分け：2026年9月4日」にある。

PreToolUse、PostToolUse、UserPromptSubmitでエージェントに行動を促す際は、`hookSpecificOutput.additionalContext`を第一の通知手段として用いる。
各フィールドがターン継続を強制するかどうかは、以下の表に従う。
標準エラー出力（stderr）は、終了コード2による遮断（block）と組み合わせる場合に限って使用する。
`systemMessage`は、ユーザーの判断や操作に影響する重要な情報通知と、エージェントに操作や是正を求める通知のユーザー向けの写しに使う（失敗しない自動補正の発動など、ユーザーの対応を必要としない定常的な事象には使わない）。
写しは、エージェントが何を指摘されたかをユーザーが画面で読めるようにするためのもので、エージェント宛ての欄の内容と配送は元のまま保つ。
実行ごとの測定値や規範本文の配送のように情報を渡すだけの通知は、画面が埋まるため写しの対象から外す。
StopやSubagentStopでターン継続を強制する場合、振り返りの誘導といったエラー相当の遮断には`decision: "block"`と`reason`を使い、想定内の助言には`hookSpecificOutput.additionalContext`を使う。

| フィールド | 表示先 | 用途 |
| --- | --- | --- |
| `hookSpecificOutput.additionalContext` | エージェント | 行動を促す主要な通知手段。PreToolUse・PostToolUse・UserPromptSubmitでは継続を強制せず、Stop・SubagentStopでは継続を強制する |
| `hookSpecificOutput.updatedToolOutput` | エージェント | PostToolUseでモデルへ渡すツール結果の置き換え。同じイベントの複数のhookは元の出力に対して並行して動作し、最後に返された置き換え結果が採用される |
| `reason` | エージェント（`decision: "block"`時のみ） | blockと併用する場合の理由欄 |
| `permissionDecisionReason` | deny時はエージェント、allow/ask時はユーザーのみ | PreToolUseの決定理由 |
| `systemMessage`・`stopReason` | ユーザーのみ | 情報通知、是正を求める通知の写しおよび`continue: false`時の終了メッセージ |
| `decision.*` | PermissionRequest専用 | 許可や拒否の決定。`hookSpecificOutput`の直下に配置する |

deny時の`permissionDecisionReason`と`hookSpecificOutput.additionalContext`はどちらもエージェントに届くため、片方に統一する（努力目標）。

`decision: "block"`の動作はイベントごとに異なる。
StopやSubagentStopではセッション停止を防いでターン継続を強制し、PostToolUseでは直前のツール結果にblock理由を添えて返す。
PreToolUse、PostToolUse、UserPromptSubmitで挙動の強制が不要な場合は、`additionalContext`単独で出力する。

block、warn、noticeの各通知本文の整形（整形関数の利用、`fix`の要否、種別、繰り返した際の要約や重要度引き上げ）は、`claude-hooks-messages-format.md`「通知本文の整形」に従う。

警告専用のPreToolUse出力では`hookSpecificOutput.additionalContext`のみを返し、`permissionDecision`は省略する（通常の権限判定フローが適用される）。
PreToolUseの`additionalContext`は、その呼び出しの実行を止めず、エージェントが読むのは呼び出しの結果と同時である。
その呼び出しを変えさせる目的には使えず、作用するのは次の操作からである。
その呼び出しを別の手段へ替えさせるには、`permissionDecision: "deny"`による拒否が要る。
監査記録は`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/claude-hooks-output.md：出力フィールドの使い分け：2026年11月11日」にある。

### 出力上限

[公式HooksリファレンスのJSON output](https://code.claude.com/docs/en/hooks#json-output)では、`additionalContext`、`systemMessage`、`initialUserMessage`の各文字列、および通常の標準出力に10,000文字の上限が定められている。
文字数は、JSONでは各フィールドの値ごとに、通常の標準出力では全体でカウントされ、同一イベントで動く複数のhookも個別にカウントされる。
上限を超過した場合、Claude Codeは全文をセッション内の一時ファイルに保存し、ファイルパスと先頭最大2,000文字のプレビュー表示に置き換える。
Claude Code自身がモデルにそのファイルの読み取りを促すことはないため、確実に届ける必要がある情報は10,000文字の上限内に収める。

公式資料の10,000文字制限では、PreToolUseが終了コード2で返す標準エラー出力は対象外である。
標準エラー出力44,224文字が、接頭辞を含めて44,346文字のツール結果として全文届いた観測記録がある。
ただし、この数値は特定の条件下で観測された配送量であり、標準エラー出力の上限を恒久的に保証するものではない。
監査記録は`docs/development/audit-records.md`の「agent-basics/skills/writing-standards/references/claude-hooks-output.md：出力上限：2026年10月20日」にある。

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| PermissionRequest・UserPromptSubmitに関する判断・作業の前 | `claude-hooks-output-events.md` |
| 組織向け設定に関する判断・作業の前 | `claude-hooks-output-organization.md` |
