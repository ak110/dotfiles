# 監査記録: 観測と実行結果

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-basics:writing-standards/references/investigation.md：実行結果の検証：2026年10月18日（2）

観測した日付は2026年10月18日、版数はClaude Code 2.1.302とcodex-cli 0.163.0である。
`atk wi process-loop`と`atk run-skill`の可用性確認の固定プロンプトで、変更前の起動形（本作業の候補のモデルとeffortの通常起動）と変更後の軽量起動を1回ずつ比べた。
Claudeの変更前は`opus[1m]`・`medium`で、入力・キャッシュ作成・キャッシュ読取の合計が41,574トークン、`~/.claude/projects`の記録が1件増えた。
変更後は`haiku`・`low`に`--no-session-persistence --setting-sources "" --tools ""`を付けた。
さらに`--strict-mcp-config --disable-slash-commands --system-prompt <文> -- <プロンプト>`を付けて起動した。
合計は630トークン、記録の増加は0件で、`modelUsage`は`claude-haiku-5-5`へ解決された。
Codexの変更前は`gpt-6.1-sol`・`medium`で`turn.completed`の`input_tokens`が30,044、変更後は`gpt-6-luna`・`low`に`--ignore-user-config --ignore-rules`を付けて27,130だった。
`codex exec --model luna`はChatGPT認証で`The 'luna' model is not supported when using Codex with a ChatGPT account.`を返して終了コード1となった。
Codexは信頼されていないGit外のディレクトリでは`--skip-git-repo-check`なしの起動を拒否するため、Codexの計測は空のGitリポジトリをcwdにした。

再検証では空のmanaged-temp（Codexは空のGitリポジトリ）をcwdにして、変更前と変更後の可用性確認の起動形を1回ずつ実行する。
Claudeは`result`イベントの`usage`の入力・キャッシュ作成・キャッシュ読取と`~/.claude/projects`の記録ファイルの有無を、Codexは`turn.completed`の`usage`と`CODEX_HOME`のrolloutの有無を比べる。
