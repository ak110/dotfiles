---
name: codex-exec
description: >
  plan-file-finalizer・plan-impl-executor・careful-reviewから明示的に呼ばれ、
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

## 入力

呼び出し元から次を受け取る。

- 作業ディレクトリの絶対パス
- 実行手順referenceとtask referenceの絶対パス
- 計画、品質規範、プロジェクト規範などtask referenceが要求する資料の絶対パス
- 作業ディレクトリ、対象、完了条件だけで構成したタスク本文
- `レビュー系`または`実装・修正系`の系統
- codex経路を継続する場合の`threadId`
- Claude代替を継続する場合の前回応答全文

用途固有referenceは次の組とする。frontmatterで本スキルを読み込んだだけでは
referenceを読み込んだと扱わない。呼び出し元と委譲先の双方が絶対パスで全組をReadする。

| 用途 | 実行手順reference | task reference |
| --- | --- | --- |
| 計画機械チェック・修正 | `plan-codex-review.md` | `plan-codex-review-fix-task.md` |
| 計画レビュー | `plan-codex-review.md` | `plan-codex-review-task.md` |
| 計画実装・修正 | `plan-codex-implementation.md` | `plan-codex-implementation-task.md` |
| 実装差分レビュー | `plan-codex-implementation-review.md` | `plan-codex-implementation-review-task.md` |

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
レビュー系と実装・修正系の`threadId`は混同しない。

## Claude代替経路

ToolSearchでcodex MCPを解決できない場合だけ、
呼び出し元がAgentツールで`subagent_type: claude`へ切り替える。
利用限度到達を応答から確認した場合も同じ経路へ切り替える。
本スキルの起動元が`plan-file-finalizer`または`plan-impl-executor`でありAgentツールを利用できない場合は、
`route: unavailable`を返して呼び出し元へ代替起動を要求する。

- 方針と期待結果が確定した実装・機械的修正は`model: sonnet`を使う
- 設計判断、重大指摘の採否、計画方針の再構成を含む作業は`model: opus`を使う
- `name`と`run_in_background`を省略し、実行結果から受領経路を判定する
- 毎回新規起動し、同じ系統の前回応答全文と未完了事項をプロンプトへ含める
- Codex経路と同じ実行手順reference、task reference、資料を絶対パスでReadさせる
- タスク本文は作業ディレクトリ、対象、完了条件だけに限定する
- 待機表明だけで終了せず、待機対象の結果を含む完了報告を1回で返す指示を含める

呼び出し元はClaude代替の応答全文を窓口へ戻し、窓口が実測と照合する。
利用した経路は実際の実行結果に基づいて1回だけ報告し、予定した経路を実績として扱わない。

## 出力

呼び出し元へ次を返す。
呼び出し元固有の`status`・`changed`・`verification`・`unplanned`などの完了報告欄は定義しない。

```text
route: codex | claude | unavailable
thread_id: <codexのthreadId。Claude代替または利用不能では「なし」>
response:
<委譲先の応答全文。利用不能では理由と呼び出し元への要求>
```

レビュー担当による成果物変更を委譲先応答または呼び出し元の実測で検知した場合、
変更内容を採用せず呼び出し元へその事実を返す。
