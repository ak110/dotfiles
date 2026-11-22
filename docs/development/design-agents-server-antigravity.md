# Antigravity CLI backendの設計記録

[設計記録の索引](design.md)から参照する、Antigravity CLI backendの記録である。

`agents_server`はengineとしてAntigravity CLI（`agy`）を持つ。

委譲起動は`modelProvider: gemini`の明示選択、子プロセスに渡る`GEMINI_API_KEY`、対応版の3条件を満たす場合だけ行う。
各turnでAPIキー方式を起動引数に固定する。
版数確認にはキーを渡さず、個人ログインで再試行しない。
[公式FAQ](https://antigravity.google/docs/faq)と[CLI認証の説明](https://antigravity.google/docs/install?tab=cli)（2026年11月22日確認）は、個人プランの第三者ラッパーとGemini APIへの直接認証を区別する。
個別のラッパーに対する承認とは扱わない。

工程別モデル設定とpresetへは加えない。
Antigravity CLIは日本語の技術文書の推敲だけを担い、Claude CodeとCodexと同格の常用engineとして扱わないためである。
委譲先が失敗で終了した場合は、共通の`cli_turn.py`から継承する`unavailable_reason`が標準エラー出力か失敗の本文を除外理由として返し、記録した除外理由は全てそのengineを除外する根拠になる。

Antigravityのexploreも通常の候補選択と開始・再開処理を使う。
読取責務は他backendと同じ共通プロンプトへ置き、実際の可用性失敗だけを候補切替へ使う。

委譲先には`--dangerously-skip-permissions`を付ける。
原稿ファイルの書き換えまでを任せる用途であり、Antigravity CLIの非対話モードは対話確認を持たず、
agent-toolkitのhookによる遮断もAntigravity CLIへ及ばないためである。
この判断はユーザーが確定した。
Antigravity CLIの権限を見直す作業は、この記録を根拠として扱う。

導入のステップ（`pytools/_internal/setup_agy_cli.py`）は取得・導入の失敗を警告に留め、失敗を持たない`PostApplyOutcome`を返す。
公式インストーラーはバイナリ本体を`*.run.app`のエンドポイントから取得するため、バイナリの取得先ホストを遮断するネットワークでは導入できない。
他のステップと同じく例外で終えると、取得先ホストへ接続できない環境では`update-dotfiles`が毎回終了コード1になり、他のステップの失敗が埋もれる。
この判断もユーザーが確定した。
導入のステップを見直す作業は、この記録を根拠として扱う。
更新はAntigravity CLI自身が実行中に自動で行うため、導入ステップはランチャーが無い場合だけインストーラーを実行する。

statuslineは`claude-statusline`の`agy-statusline`モードが担う。
Antigravity CLIが渡すJSONはClaude CodeのstatusLine入力と項目が完全には重ならないため、
受け取れる項目だけを同じ区切りと日本語ラベルへ射影する。
配布アセットを1個に保つため、モードを増やす形で既存のバイナリへ同居させる。
