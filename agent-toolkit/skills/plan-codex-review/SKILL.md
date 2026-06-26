---
name: plan-codex-review
description: >
  plan-modeスキルのcodexレビュー工程から呼び出す。
  呼び出し時は次の情報を引数に渡す。計画ファイルの絶対パス、プロジェクトルートの絶対パス、初回か継続かの別、継続時は`threadId`（MCP版）または`SESSION_ID`（CLI版）、追加のレビュー観点（任意）
context: fork
agent: Explore
allowed-tools: mcp__codex__codex mcp__codex__codex-reply Bash(codex exec*)
---

# 計画ファイルのcodexレビュー委譲

codexレビューを1ラウンド実行して指摘全文を報告する。

`mcp__codex__codex` / `mcp__codex__codex-reply`が利用可能な環境では常時MCPツール版を使う。
CLIフォールバック版はMCPが利用不可な環境専用とする。
CLI実行が失敗した場合は再試行可否のユーザー確認で判断停止せず、MCP利用可能性を再点検したうえで利用可能なら経路を切り替えて続行する。

## プロンプト構築

初回プロンプトは以下のテンプレートで構成する（MCP・CLI共通）。
`{review_standards_body}`には`${CLAUDE_SKILL_DIR}/../review-standards/SKILL.md`をReadで取得し、
H1見出し以降を埋め込む。

```text
{plan_full_path} この計画ファイルをレビューして。

レビュー観点:

- 発見した指摘は重大度ラベルを付けて全件報告する（採否の選別はメイン側で行う）
- 全件報告の中で、同種カテゴリの違反が同ファイル内に複数出現する場合は代表例1件に集約し、残箇所は箇所数のみ示す
- `agent-toolkit:*`スキルや`plan-*-reviewer`などの識別子はClaude Code側の仕組みでcodex側からは存在確認できないため、
  これらの実在性に関する指摘は不要
- 計画ファイル自体は実装着手後に廃棄される一時的な作業文書であり、永続的なプロジェクト成果物ではない。
  通常のコード・ドキュメントレビューでは指摘すべき以下の観点も、計画ファイルでは指摘対象外とする
  - 記述スタイル（章構成・段落構成・表現選択・書き方）への指摘。記述間の矛盾は対象に含む
  - 実装時にエージェントが判断可能な細部（変数名・エラーメッセージ文言・小規模なループ構造・局所的な制御フローなど）への指摘
- `agent-toolkit:agent-standards`「文書サイズ上限」節の対象ファイル（`SKILL.md`・サブエージェント定義・`references/`配下など）を編集する計画では次の必須記載を確認する。縮減根拠4類型はSSOT違反・自明導出・公式ドキュメント代替可能・上位指針への統合とし、`trim-agent-docs`委譲時も同じ
  - 現行行数: `## 調査結果`に対象ファイルの現行行数（`wc -l`実測値）が記録されているか
  - ハードリミット超過対応: 改訂後の最終形がハードリミット220行未満に収まる見込みか（接近リスクがある場合は`### エージェント判断`に`wc -l`実測の試算行数記載があるか）
  - 縮減根拠: 改訂で既存節縮減を伴う場合に縮減対象節名と縮減根拠4類型が`## 変更内容`へ明記されているか
  - 既存違反遡及スキャン: 新規規範を追加または改訂した場合の既存違反遡及スキャン結果が`## 調査結果`へ反映されているか

{review_standards_body}
```

継続時のプロンプトは`計画ファイルを更新したので再レビューして。`とする。

## 実行方法

### MCPツール版

`ToolSearch`でスキーマを取得した直後の最初のアクションとして該当MCPツールを呼び出す。プロンプト生成・パラメーター整形のための自己点検をツール呼び出し前に続けない。これは初回・継続を問わず、`mcp__codex__codex-reply`呼び出しも対象に含める。

- 初回: `mcp__codex__codex`を以下の引数で呼び出す
  - `cwd`: `"{project_directory}"`
  - `sandbox`: `"danger-full-access"`
  - `prompt`: 初回プロンプト
- 継続: `mcp__codex__codex-reply`（`threadId`: 前回の戻り値、`prompt`: 再レビュープロンプト）

### CLIフォールバック版

初回:

```bash
set -o pipefail && codex exec --dangerously-bypass-approvals-and-sandbox --cd "{project_directory}" \
  --output-last-message "{plan_full_path}.review.md" \
  "{初回プロンプト}" \
  2>&1 | grep "^session id:"
```

session idの抽出に失敗、またはcodexがエラー終了した場合は、grepを外して`codex exec ... 2>&1`で再実行し全文を確認する。

継続:

```bash
codex exec resume --dangerously-bypass-approvals-and-sandbox \
  --output-last-message "{plan_full_path}.review.md" {SESSION_ID} \
  "{再レビュープロンプト}"
```

`SESSION_ID`は初回コマンドの`grep "^session id:"`で抽出する。
`--last`は並列セッション実行時に意図しないセッションを再開する恐れがあるため使用しない。
レビュー結果は`{plan_full_path}.review.md`に出力されるのでReadツールで読み取る。

## 報告

codexの指摘全文と継続用の`threadId`（CLIの場合は`SESSION_ID`）を要約・省略せず返す。
MCPもCLIも利用できない場合はその事実のみを報告して終了する。
