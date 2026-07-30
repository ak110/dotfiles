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
- 単独で実行できる完結したタスク本文
- `レビュー系`または`実装・修正系`の系統
- codex経路を継続する場合の`threadId`
- Claude代替を継続する場合の前回応答全文

用途固有referenceは次の対応とする。
frontmatterで本スキルを読み込んだだけではreferenceを読み込んだと扱わない。
呼び出し元は`${CLAUDE_PLUGIN_ROOT}/skills/codex-exec/references/<ファイル名>`をReadし、
必要事項をタスク本文へ含める。

| 用途 | reference |
| --- | --- |
| 計画レビュー | `plan-codex-review.md` |
| 計画実装・修正 | `plan-codex-implementation.md` |
| 実装差分レビュー | `plan-codex-implementation-review.md` |

レビュー用途のタスク本文には、対象を読み取り専用とし成果物を変更しない指示を含める。

## codex経路

1. ToolSearchで`mcp__codex__codex`と`mcp__codex__codex-reply`のスキーマを解決する
2. `threadId`が無い初回は`mcp__codex__codex`を呼び出す
   - `cwd`には受領した作業ディレクトリの絶対パスを指定する
   - `sandbox`には`danger-full-access`を指定する
   - `config.model_reasoning_effort`には`medium`を指定する
3. 同じ系統の`threadId`がある場合は`mcp__codex__codex-reply`で継続する
4. 指定モデルが利用できない場合だけ、同じ経路で利用可能な既定モデルへ切り替えて再試行する
5. 一時的な失敗は同じ経路で再試行する

初回応答の`threadId`は呼び出し元へ返し、同じ系統の後続処理へ再利用させる。
レビュー系と実装・修正系の`threadId`は混同しない。

## Claude代替経路

ToolSearchでcodex MCPを解決できない場合、または利用限度到達を応答から確認した場合だけ、
Agentツールで`subagent_type: claude`へ切り替える。

- 方針と期待結果が確定した実装・機械的修正は`model: sonnet`を使う
- 設計判断、重大指摘の採否、計画方針の再構成を含む作業は`model: opus`を使う
- `name`と`run_in_background`を省略したforeground起動とする
- 毎回新規起動し、前回応答全文と未完了事項をプロンプトへ含める
- 用途固有referenceが指定する品質規範から、タスクへ適用する節本文を起動プロンプトへ引用転記する
- スキル名・節名・絶対パスの列挙を、規範本文の引用転記に代用しない
- 完了報告をツール戻り値で返し、待機表明だけで終了しない指示を含める

## 出力

呼び出し元へ次を返す。
呼び出し元固有の`status`・`changed`・`verification`・`unplanned`などの完了報告欄は定義しない。

```text
route: codex | claude
thread_id: <codexのthreadId。Claude代替では「なし」>
response:
<委譲先の応答全文>
```

レビュー担当による成果物変更を委譲先応答または呼び出し元の実測で検知した場合、
変更内容を採用せず呼び出し元へその事実を返す。
