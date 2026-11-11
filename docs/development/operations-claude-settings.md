# 運用機能: Claude Codeの質問と出力設定

## 質問・ダイアログのタイムアウトの配布

Claude Codeの`askUserQuestionTimeout`と`dialogExpiry`は`share/claude_settings_json_managed.json`でいずれも`never`を配布する。
`askUserQuestionTimeout`の対象は`AskUserQuestion`の選択質問だけである。
権限確認や計画承認は自動継続しない。
`askUserQuestionTimeout`による自動継続は、Remote Controlのbridgeが接続したセッションではarmedされない。
bridgeが接続すると、ダイアログの内部表現が持つ`hasExternalRacer`は真になる。
自動継続のarmed条件には、この値が偽であることが含まれるためである。
`~/.claude/settings.json`は`remoteControlAtStartup`が真であり、対話TUIのセッションはこの条件へ該当する。
armedされた場合も、端末フォーカスが`focused`の間は自動継続しない。
2.1.319の待機処理（`CLAUDE_AFK_TIMEOUT_MS`を参照する関数）は毎秒フォーカスを調べ、`focused`なら待機の起点を現在時刻へ更新するため、期限に達しない。
タイマー発火後のユーザー操作も自動継続を取り消す。
フォーカスは端末のfocus-in報告か、`blurred`でない間のキー入力・マウス押下で`focused`になり、focus-out報告まで続く。
このため、端末多重化ツールがペインのfocus-inを報告する環境では、常駐実行のペインをアクティブにしているだけで、設定した期限は満了しない。
2026年11月7日、herdrのペインで実行したprocess-loopの統括の質問が5分で自動継続せず、ユーザーの回答まで約3時間52分待った。
この事象はこの条件によると推定している（herdrがfocus-inを報告することは観測していない）。
2026年11月10日のユーザー回答に従い、process-loopのClaude統括も`AskUserQuestion`を使える。
フォーカスによる待機延長はユーザーが離席時のペイン状態に気を付けて対処し、質問を拒否してUWIへ固定するhookは撤去した。
`dialogExpiry`は対話TUIが描画する`AskUserQuestion`を対象にしない。
`dialogExpiry`が未設定の場合の値を解決する関数の呼び出し元は、bridgeへ転送したダイアログの駐留、制御プロトコルのユーザーダイアログ要求、cross-sessionのHELDメッセージの失効の3箇所だけである。
`AskUserQuestion`の転送はこの3箇所のいずれにも該当しない。
このため`atk wi process-loop`のClaude起動は`--settings`へ`remoteControlAtStartup`の偽を渡し、常駐実行のセッションでbridgeを接続しない。
常駐実行のセッションでは別端末からの回答ができなくなるが、質問待ちに上限を与える利益を優先する。
本節の記述は2026年9月7日にClaude Code 2.1.263のバイナリを実読して確定し、フォーカスの判定は2026年11月7日に2.1.319のバイナリで再確認した。
再検証ではバイナリから`hasExternalRacer`を設定する箇所と、自動継続のarmed条件でこの値が偽であることを要求する箇所を読む。
フォーカスの判定は、`CLAUDE_AFK_TIMEOUT_MS`を参照する待機関数と、`terminalFocus`を更新する入力処理（focusイベント、キー入力、マウス押下）を読む。
続けて`dialogExpiry`が未設定の場合の値を解決する関数の呼び出し元を列挙し、`AskUserQuestion`の転送が含まれないことを確認する。

本節が記録する配布の事実が規範の判断へ及ぼす影響は、[concepts.md](concepts.md)の「確認・合意の運用」が保持する。

`dialogExpiry`の対象はリモートクライアントへ転送された権限ダイアログとユーザーダイアログが回答を待って駐留できる上限、
およびHELD状態のcross-sessionメッセージが承認を待つ時間である。
上限を超えるとキャンセルまたは拒否付きのdropへ解決するため、エージェントは期限切れとユーザー本人の拒否を区別できない。
リモートクライアントが接続していないローカル専用の権限プロンプトは影響を受けない。
`~/.claude/settings.json`は`remoteControlAtStartup`が真であり、権限ダイアログが転送されるため、
`dialogExpiry`を設定せず`5m`のままでは離席が5分を超えた時点で自動キャンセルされる。
環境変数`CLAUDE_CODE_USER_DIALOG_TIMEOUT_MS`を設定した環境では、設定ファイルよりも環境変数の値を優先する。

`atk wi process-loop`のClaude起動だけが`--settings`で両設定の値と`remoteControlAtStartup`を明示する。
自律モードでは無期限の駐留が工程の停止を招くため、配布値の`never`を常駐実行だけ有限値へ上書きする。
値は実行環境のプロンプトキャッシュTTLに合わせ、TTLが5分の環境（Amazon Bedrock、Claude Platform on AWSなど）では`60s`、
TTLが1時間の環境では`5m`とする。
判定は委譲待機のcron間隔と同じ`agent-toolkit/agent_toolkit/_common/wait_schedule.py`の
プロンプトキャッシュTTL判定を用いる。
ユーザーが`~/.claude/settings.json`の`promptCacheTtl`でTTLを明示した環境では、明示されたTTLを判定に用いる。
CLI設定はユーザー設定より優先されるため、常駐実行ではこの値が適用される。
Claude起動分岐では`CLAUDE_CODE_RETRY_WATCHDOG=1`だけを子プロセス環境へ設定する。
`API_TIMEOUT_MS`、
`CLAUDE_STREAM_IDLE_TIMEOUT_MS`および`CLAUDE_CODE_MAX_RETRIES`はprocess-loopが子プロセス環境へ常に設定する値に含めない。
該当する障害を確認した環境でだけ、原因に対応する変数を個別に設定する。
Codex起動と`update-dotfiles`実行の環境へはClaude専用の値を渡さない。

## Claude CodeのBash出力の上限

`share/claude_settings_json_managed.json`はClaude Codeの`bashOutputMaxChars`へ62,000を配布する。
`pytools/_internal/update_claude_settings.py`がこの値を`~/.claude/settings.json`へ反映し、Claude CodeのBashとPowerShellは62,000バイト以下の出力を退避せずに全文で返す。
値はCodexへ配布する`tool_output_token_limit = 20000`（`share/codex_config.toml`）をバイトへ換算したもので、1回のツール出力で受け取る量を両ホストでそろえる。
換算には`agent-toolkit/agent_toolkit/_hooks/pretooluse/large_reads.py`が使う1トークンあたり3.10バイトを用いる。
62,000バイトを超える出力は退避され、agent-toolkitのPostToolUseが抜粋を保存先と次の操作を示す本文へ置き換える。
置き換えの設計は`design-hooks-posttooluse.md`「退避したシェル出力の抜粋の置き換え」にある。
`share/claude_settings_json_managed.json`はコメントを持てないため、値の理由は本節だけが保持する。

本体の設定スキーマにある`bashOutputMaxChars`の説明は次のとおりである。

```text
How many characters of a successful Bash or PowerShell command's output Claude receives inline (default 30000; values clamp to 4000-128000)
```

指定しない場合は50,000バイト（19,774文字）のファイルも退避されるため、比較はバイト数で行われる。
観測は2026年10月7日にClaude Code 2.1.291で行った。
`claude -p --settings share/claude_settings_json_managed.json --model haiku --output-format stream-json --verbose`へファイルを`cat`させた。
61,500バイトのファイルは`persistedOutputPath`を持たず全文で返った。
63,000バイトのファイルは`<persisted-output>`で返り、`persistedOutputSize`は63000だった。

同じ形で上限の前後の大きさのファイルを`cat`させ、ストリームの`tool_use_result`の`persistedOutputPath`の有無で再検証する。
上限値の解釈が変わった疑いがある場合は、本体のバイナリから`bashOutputMaxChars`の説明を確かめる。
BashツールとPowerShellツールの`maxResultSizeChars`が指定値を4,000から128,000へ収める関数も確かめる。
検索の手順は`.claude/skills/develop-dotfiles/references/host-binaries.md`「ホスト本体のバイナリの検索」に従う。
