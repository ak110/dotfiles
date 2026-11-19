# セッション記録のMarkdown出力

Claude Code・Codexのセッション記録（「会話ログ」「セッション履歴」）をMarkdownへ出力・保存する依頼で使う手順書である。
記録ファイルへのアクセスと`atk`コマンドが要る。
使えない環境では、ユーザーが提示した会話の本文だけをMarkdownへ整形し、情報の出所をその提示の範囲に限る。

1. 対象を決める。
   現在の会話は、Claude Codeのメインでは、SessionStartの通知にある「この会話のセッションID」を使う（`agent-toolkit:/share/rules/rules-main.claude-code.md`「自身の会話の記録」）。
   Claude Codeのメイン以外では環境変数`CLAUDE_CODE_SESSION_ID`、Codexでは`CODEX_THREAD_ID`を使う。
   ほかの会話は、セッション識別子、`--project-dir`、`--all`のいずれかで指定する。
2. `atk agents logs <対象> --format markdown`で変換する。
   範囲と出力の指定（`--latest`、`--include-thinking`、`--include-subagents`、`--output-dir`など）は`atk agents logs --help`で選ぶ。
3. 見出しや説明文を書き足す時は`agent-basics:writing-standards`に従う。

返す報告に含める項目: 出力したファイルの絶対パス、対象のセッション、出力に含めなかった範囲。
