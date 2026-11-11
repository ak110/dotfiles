# 監査記録: 観測と実行結果

[監査記録の索引](audit-records.md)に収録対象と記録の書式を示す。

## agent-basics/skills/check-execution/references/diagnostics.md：読む出力：2026年10月3日

2026年10月3日、pyfltr 3.19.9でJSONLの要約と診断の粒度を確認した。
`uv run --frozen pyfltr run --commands=textlint --no-fix agent-toolkit/share/add-wi.parent.md`は終了コード0だった。
診断レコードはseverity=warningのメッセージを1件持ち、コマンド結果はsucceeded・diagnostics=1、要約の`commands_summary.needs_action.warning`は0、`diagnostics`は1だった。
導入済みパッケージの`pyfltr.output.jsonl._build_summary_record`も、各コマンドのstatusを数える処理と診断を集計する処理を分けていた。

再検証では導入版を取得し、warningの診断を持つMarkdownへ同じtextlintの起動形を適用する。
JSONLのdiagnosticのmessages、commandのstatus・diagnosticsとsummaryの`commands_summary.needs_action.warning`を比較する。
出力の集計は同じ保存ファイルを使い、件数0だけから診断の不在を判断しない。
導入済みパッケージの同関数も読み、要約が数える値と診断の生成を確かめる。

## agent-basics/skills/writing-standards/references/investigation.md：実行結果の検証：2026年10月9日

2026年10月9日、Codex CLI 0.162.0の公式ソース（タグ`rust-v0.162.0`）を読解した記録に基づく。
`codex-rs/exec/src/cli.rs`の`--ephemeral`と`codex-rs/exec/src/lib.rs`の適用はセッションの永続化を抑止する。
認証処理は`codex-rs/login/src/auth/manager.rs`と`codex-rs/login/src/auth/storage.rs`にあり、ChatGPTトークンの期限が5分以内か、最後のrefreshから8日を超える条件で更新し、認証ストレージへ保存する。
`--ephemeral`はこの更新・保存を抑止しない。
実機でrefreshを新たに起こす観測は行っていない。

再検証では同じ公式タグのCLIオプション、execへの適用、認証更新条件と保存処理を対応付けて読む。
実機で測定する場合は公開された状態隔離手段と通常認証の成立を先に確認し、同じ実体のmtimeなどを前後で比較する。
外部認証情報を隔離先へ複製しない。
通常利用と既存のテスト隔離へ一律の隔離を追加する根拠にはしない。

## agent-basics/skills/writing-standards/references/investigation.md：実行結果の検証：2026年10月18日（1）

観測した日付は2026年10月18日、版数はClaude Code 2.1.302である。
再検証では再開・分岐を伴わない新規の`claude -p`を空のmanaged-tempで起動する。
まず`--allowedTools Read`だけを指定して実行する。
次に`--tools "" --strict-mcp-config --mcp-config '{"mcpServers":{}}'`も指定して実行する。
両実行のstream-jsonの`system`/`init`にある`tools`と`mcp_servers`を比較する。

## agent-toolkit/skills/user-confirmation-and-report/references/investigation.md：実行結果の検証：2026年10月18日（2）

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
