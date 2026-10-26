---
name: export-session
description: >
  Claude CodeやCodexのセッション記録をmarkdownに出力・保存する依頼（「会話ログ」「セッション履歴」の出力・保存を含む）で使う。
# descriptionの「会話ログ」「セッション履歴」は、起動条件をユーザーが依頼で使う語と一致させるために残す。
---

# セッション記録のmarkdown出力

セッション記録を取得して保存するには、対象の記録ファイルにアクセスできるファイル操作機能と`atk`コマンドが必要である。これらが利用できない環境では、ユーザーから提示された会話テキストのみをMarkdown形式に整形し、情報の出所をその提示範囲に限定して扱う。ホスト環境の記録ファイル自体の取得やファイル保存は、CLIツールを実行可能なClaude CodeまたはCodex環境で実施する。見出しや説明文を記述する際は、[文章の品質基準](references/quality-standards.md)に準拠する。

`atk agents logs <対象> --format markdown`コマンドを実行して、Claude CodeおよびCodexのセッション記録をMarkdown形式へ変換する。
出力対象は、セッション識別子、`--project-dir`、または`--all`のいずれかのオプションで指定する。
現在実行中のセッション識別子は、Claude Codeのメインエージェント環境では`rules-main.claude-code.md`「自身の会話の記録」で案内されるSessionStart通知内の「この会話のセッションID」の値を使用する。
Claude Codeのメイン以外のエージェントでは環境変数`CLAUDE_CODE_SESSION_ID`を、Codex環境では環境変数`CODEX_THREAD_ID`を使用する。
対象範囲や出力形式（`--latest`、`--include-thinking`、`--include-subagents`、`--output-dir`など）の指定オプションは、`atk agents logs --help`を参照して選択する。

## 名指し参照の読込方法

本文内の各参照における適用条件を確認し、選択した資料を読み込み、指定箇所に注目してから、その基準を用いた具体的な判断に進む。起動時の行に指定された資料は、起動時の本文とともに提供される。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `references/quality-standards.md` | 判断 | `read:agent-toolkit:*/skills/export-session/references/quality-standards.md` |
