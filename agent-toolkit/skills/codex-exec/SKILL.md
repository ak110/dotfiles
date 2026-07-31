---
name: codex-exec
description: >
  plan-file-finalizer・plan-impl-executorから明示的に呼ばれ、
  codex MCPまたは汎用エージェントへ作業を委譲するときに起動する。
user-invocable: false
# 編集時の注意点:
# agents定義は単一Markdownで専用referenceを持てないため、計画ファイル処理に固有の
# レビュー・実装・実装差分レビュー用プロンプトを本スキル配下referencesへ置く。
# SKILL.md本体は共通の委譲経路だけを扱う。
---

# codex委譲

codex MCPを優先し、利用できない場合だけ汎用エージェントへ切り替える。
用途固有の作業内容は呼び出し元がreferenceから構成し、本スキルは接続・継続・代替経路だけを担う。

Skillツールの成功応答は本スキルの読み込み完了を示し、委譲結果の待機状態を示さない。
呼び出し元は成功後、既存のMCPスキーマの有無を確認し、ToolSearch、MCP初回接続、
MCP継続接続、Claude代替判定のうち該当する工程へ直ちに進む。
`plan-file-finalizer`と`plan-impl-executor`はSkill、ToolSearch、MCP接続、委譲の順序を共通で適用する。

## 入力

呼び出し元から次を受け取る。

- 作業ディレクトリの絶対パス
- 実行手順referenceとtask referenceの絶対パス
- 計画、品質規範、プロジェクト規範などtask referenceが要求する資料の絶対パス
- 作業ディレクトリ、対象、完了条件だけで構成したタスク本文
- `計画レビュー系`、`計画準拠実装レビュー系`、`独立実装レビュー系`、
  `実装・修正系`のいずれか
- codex経路を継続する場合の`threadId`
- Claude代替を継続する場合の系統別Agent識別子
- Claude代替を継続する場合の前回応答全文

用途固有referenceは次の組とする。frontmatterで本スキルを読み込んだだけでは
referenceを読み込んだと扱わない。呼び出し元と委譲先の双方が絶対パスで全組をReadする。

| 用途 | 実行手順reference | task reference |
| --- | --- | --- |
| 計画機械チェック・修正／計画レビュー | `plan-codex-review.md` | 同reference「用途別task reference」で選択 |
| 計画実装・修正 | `plan-codex-implementation.md` | `plan-codex-implementation-task.md` |
| 計画準拠の実装差分レビュー | `plan-codex-implementation-review.md` | `plan-codex-implementation-plan-review-task.md` |
| 独立した実装差分レビュー | `plan-codex-implementation-review.md` | `plan-codex-implementation-independent-review-task.md` |

task referenceは委譲先自身が守る作業契約だけを記載する。
実行手順referenceは経路選択、継続、検収、再委譲を記載する。

## codex経路

1. ToolSearchで`mcp__codex__codex`と`mcp__codex__codex-reply`のスキーマを解決する
2. `threadId`が無い初回は`mcp__codex__codex`を呼び出す
   - `cwd`には受領した作業ディレクトリの絶対パスを指定する
   - `sandbox`には`danger-full-access`を指定する
   - `config.model_reasoning_effort`には`medium`を指定する
3. 同じ系統の`threadId`がある場合は`mcp__codex__codex-reply`で継続する
4. 指定モデルが利用できない場合だけ、同じ経路で利用可能な既定モデルへ切り替えて再試行する
5. 一時的な失敗は同じ経路で再試行する

Codexへ渡すプロンプトは、referenceと資料の絶対パス、および作業ディレクトリ、対象、完了条件だけで
構成する。Codexが利用しないClaude Code固有の経路管理、通知、受領手順を含めない。

初回応答の`threadId`は呼び出し元へ返し、同じ系統の後続処理へ再利用させる。
計画レビュー系、計画準拠実装レビュー系、独立実装レビュー系、
実装・修正系の`threadId`は混同しない。

## Claude代替経路

ToolSearchでcodex MCPを解決できない場合だけ、
呼び出し元がAgentツールで`subagent_type: claude`へ切り替える。
利用限度到達を応答から確認した場合も同じ経路へ切り替える。
本スキルの起動元が`plan-file-finalizer`または`plan-impl-executor`でありAgentツールを利用できない場合は、
`route: unavailable`を返して呼び出し元へ代替起動を要求する。

- 方針と期待結果が確定した実装・機械的修正は`model: sonnet`を使う
- 設計判断、重大指摘の採否、計画方針の再構成を含む作業は`model: opus`を使う
- `name`と`run_in_background`を省略し、実行結果から受領経路を判定する
- 同じ系統のAgent識別子があり`SendMessage`を利用できる場合は、未完了事項と追加指示だけを送り同じAgentを再開する
- Agent識別子が無い場合、`SendMessage`を利用できない場合、または送信に失敗した場合だけ新規起動し、同じ系統の前回応答全文と未完了事項をプロンプトへ含める
- 初回起動で得たAgent識別子を応答履歴とともに保持し、別の系統へ流用しない
- Codex経路と同じ実行手順reference、task reference、資料を絶対パスでReadさせる
- タスク本文は作業ディレクトリ、対象、完了条件だけに限定する
- 待機表明だけで終了せず、待機対象の結果を含む完了報告を1回で返す指示を含める

孫エージェントの完了報告は次の記録経路でも受領する。

- 記録先の確保: 初回Agent起動と各`SendMessage`再開を独立した完了報告試行として扱う。各試行の直前に`mktemp -d`で一意なディレクトリを作成し、所有者だけがアクセスできる権限へ設定する。配下の`completion.md`を完了報告の最終パスとし、試行前には作成しない
- 起動プロンプト: 初回起動と再開メッセージの双方へ、最終パスと同じディレクトリの`completion.tmp`を絶対パスで渡し、task referenceの完了報告全文を通常応答と記録ファイルの双方へ出力させる。待機、検収、後始末は当該試行で生成したマーカーだけを対象とする
- 完了時の書き込み: 孫エージェントは`completion.tmp`へ全文を書き込み、所有者だけが読み書きできる権限を設定する。`ls -l`と`wc -l`で実在と分量を確認してから、同一ディレクトリ内の`completion.md`へ原子的に改名する。最終パスの実在を完了マーカーとする
- 受領待機: 両窓口が許可済みのBashツールで、最終パスの実在を条件として1回だけ有界待機する。待機の終了状態は最終パス実在またはタイムアウトの2値とし、通常応答または完了通知だけでは完遂と判定しない
- 受領と検収: 最終パスが実在する場合は`ls -l`と`wc -l`の結果、全文、task referenceの必須欄、成果物実体を検収し、通常応答を受領できた場合は記録内容との一致も確認する
- 記録失敗: 有界待機がタイムアウトした場合は、最終パスと`completion.tmp`の実在・分量、待機コマンドの終了状態を記録し、待機を再実行せず`needs_escalation`へ移る。Claude Code 2.1.220では`TaskOutput`が全サブエージェントから除外され、backgroundサブエージェントは`TaskGet`と`TaskList`も利用できないため、孫エージェントの状態照会を条件に用いない
- 後始末: 正常検収後は、窓口自身が作成した一意なディレクトリだけを除去し、除去結果を履歴へ記録する。記録失敗時は書き込み主体の終端を観測できないためディレクトリを保持し、絶対パスを`needs_escalation`へ記録する。パスの所有関係を確定できない場合も除去しない

Agentを起動した主体は記録ファイルから受領した応答全文を実測と照合する。
呼び出し元が代替起動した場合は、検収済みの応答全文を窓口へ戻す。
利用した経路は実際の実行結果に基づいて1回だけ報告し、予定した経路を実績として扱わない。

## 出力

呼び出し元へ次を返す。
呼び出し元固有の`status`・`changed`・`verification`・`unplanned`などの完了報告欄は定義しない。

```text
route: codex | claude | unavailable
thread_id: <codexのthreadId。Claude代替または利用不能では「なし」>
agent_id: <ClaudeのAgent識別子。codex経路または利用不能では「なし」>
response:
<委譲先の応答全文。利用不能では理由と呼び出し元への要求>
```

レビュー担当による成果物変更を委譲先応答または呼び出し元の実測で検知した場合、
変更内容を採用せず呼び出し元へその事実を返す。
