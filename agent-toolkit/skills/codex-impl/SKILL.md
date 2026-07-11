---
name: codex-impl
description: >
  `plan-impl-executor`の実装工程でコード・テストコード・一般ドキュメントの
  実装タスクをcodex MCPへ委譲するときに呼び出す。
  lintエラー対応・レビュー指摘反映は`threadId`による継続呼び出しで同一codexスレッドへ戻す。
allowed-tools: mcp__codex__codex mcp__codex__codex-reply
---

# 実装タスクのcodex委譲

実装タスクをcodex MCPへ委譲して実行する手順を定める。
本スキルはfork型ではなく、呼び出し元（メインエージェント）が本文の手順に従い
`mcp__codex__codex` / `mcp__codex__codex-reply`を直接呼び出す。
lintエラー対応・レビュー指摘反映など複数回のやり取りを`threadId`の継続で行う前提のため、
サブエージェントへの委譲を介さない。

MCPが利用不可（`ToolSearch`で`mcp__codex__codex`のスキーマを取得できない、
または呼び出し自体がエラーとなる）な場合は、初回・継続の別を問わず本スキルの手順を適用しない。
`plan-impl-executor`手順「実装委譲（codex-impl / plan-implementer）の判断指針」節に従い
`plan-implementer`委譲へフォールバックする。

## プロンプト構築

初回プロンプトは以下のテンプレートで構成する。
`{plan_full_path}`は計画ファイルの絶対パス、`{タスク記述（担当範囲）}`は
計画ファイル`## 変更内容`の該当節を特定できるタスク記述とする。
`{quality_standards_body}`はコードを含むタスクとドキュメント・コメントを含むタスクで異なる。
コードを含むタスクでは`${CLAUDE_SKILL_DIR}/../coding-standards/SKILL.md`をReadで取得する。
ドキュメント・コメントを含むタスクでは`${CLAUDE_SKILL_DIR}/../writing-standards/SKILL.md`をReadで取得する。
取得した内容のH1見出し以降を埋め込む（両方に該当する場合は両方を埋め込む）。
`{formatter_linter_instruction}`は並列/単独実行区分で次のいずれかへ差し替える（初回・継続とも同じ）。

- 並列実行時: `- formatter・linterの実行は省略する（呼び出し元が全タスク完了後に一括実行する）`
- 単独実行時: `- 完了報告前にformatter・linter（例: uvx pyfltr run-for-agent）を通過させる`

```text
{plan_full_path} この計画ファイルの以下のタスクを実装して。

## タスク

{タスク記述（担当範囲）}

## 遵守事項

- 計画ファイル本文の`## 変更内容`該当節に記載された内容のみを実装対象とする
- git commit・push・タグ作成などの不可逆操作は行わない（コミットは呼び出し元が別途行う）
- 作業ツリー全体の状態を変更するgit操作（`git stash`・`git checkout`・`git reset`・`git clean`等）は行わない。
  必要と判明した場合は実行せず完了報告でその旨を明示する
- 対象外ファイルの変更は行わない。対象外ファイルの変更が必要と判明した場合は、
  実装を進めず完了報告でその旨を明示する
{formatter_linter_instruction}

## 品質規範

{quality_standards_body}
```

継続時のプロンプトは以下のテンプレートで構成する。

```text
以下の指摘に対応して修正実装して。初回に提示した遵守事項は継続して適用する。

{継続時の修正指摘全文}

{formatter_linter_instruction}
```

## 実行方法

- 初回: `mcp__codex__codex`を以下の引数で呼び出す（列挙外の引数は指定しない）
  - `cwd`: プロジェクトルートの絶対パス
  - `sandbox`: `"danger-full-access"`
  - `prompt`: 初回プロンプト
- 継続: `mcp__codex__codex-reply`（`threadId`: 初回応答で得た値、`prompt`: 継続プロンプト）
- 並列: 編集対象ファイルが独立する複数タスクは、同一メッセージ内で複数の`mcp__codex__codex`を並列呼び出しする。
  並列時は各プロンプトへ並列実行区分を適用し、全タスク完了後に呼び出し元が`uvx pyfltr run-for-agent`等のfixを
  一括実行して、残る指摘を該当タスクの`threadId`への継続呼び出しで戻す。
  単独実行時は単独実行区分を指定し、codex側にformatter・linter通過まで委ねてよい

## threadIdの管理

- 初回呼び出し応答の`threadId`を計画ファイル`## 進捗ログ`へタスクごとに記録する（記録先の正典は進捗ログとする）
- lintエラー対応・レビュー指摘反映は記録済みの`threadId`で継続呼び出しする
- `threadId`が不明、または対象スレッドが消失している場合は`plan-implementer`への修正再実装の委譲へフォールバックする

## 完了後の扱い

- codexの応答受領後の検収・検証・コミット・pushは呼び出し元メインの責務とする
- 検収はcodex応答の`changed`欄照合に代えて、計画`## 変更内容`該当節の個別指示と`git diff`実体を直接照合する
- codex応答が対象外ファイルの変更の必要性など計画側の是正を要する事項を含む場合は、
  メインが計画を是正するか`plan-implementer`委譲・メイン直接実装へ切り替える
