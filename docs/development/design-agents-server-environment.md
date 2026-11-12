# プロトコルと起動環境の設計記録

[設計記録の索引](design.md)から参照する、プロトコルと起動環境の記録である。

Codex App Serverのstdoutは、正の固定byte長で`read(n)`したchunkをbufferへ蓄積し、改行ごとにJSON-RPC recordへ復元して既存の応答・server request・通知dispatcherへ渡す。
`APP_SERVER_STREAM_LIMIT_BYTES`はsubprocess pipeのbufferと1回の読取長を有界にする値である。
1つのprotocol recordがこの値を超えても、chunkの蓄積で復元できる。
EOF時の残余も1 recordとして処理し、不正UTF-8、不正JSON、取消し、接続終了に対する既存の失敗処理を保つ。

`StreamReader.readline()`のlimitを大きくするだけの案は、その固定値を有効なJSON-RPC recordの上限にしてしまい、planやdiffを含む応答が超えた時点でreaderを停止させるため採用しない。
無制限に一度で読む案も、transportのbackpressureとメモリー使用量を有界にできないため採用しない。

backend固有のserver requestは共有公開toolを増やさず、非対話の非対応エラーとして応答する。
承認、ユーザー入力、認証token更新、attestationおよび未知methodを待機させず、対応turnをfailedへ遷移してwaiterを解放する。
通知のdeltaは進捗表示にだけ用い、終了応答で結果本文を返す。
この境界により、呼び出し側は実行中の状態を照会し、
終了後に結果を取得できる。
結果を回収し忘れたときにStopで機械的に遮断する仕組みは持たない。

却下した代替案は、Codex専用とClaude専用の2サーバーを併存させる案である。
公開tool、session状態、
hookの配送境界および継続条件がbackendごとに分断され、同じ委譲契約を二重に維持する必要が生じるため採用しない。

委譲先へ渡す実行環境は、委譲サーバープロセスの環境を起動時に1回整えることで全backendへ同時に適用する。
起動元の会話・端末・プロセスのcontextは、共有の`_common/mcp_environment.py`の`strip_conversation_context`で除去する。
この関数は内側MCPの`env_vars`と同じ除外名の集合を使う。
MCP起動時と`atk wi`のall-reposでのManager生成に適用し、各backendは整備後の環境を使う。
process-loop・run-skillと可用性判定の最上位会話も、`_atk/orchestrator.py`の`child_env`から同じ関数を使う。
サーバーのルート、書込主体、委譲元の記録情報と引き継ぎ記録先は除去前のsnapshotから確定する。
各起動が明示するownerと委譲の印は除去後に設定し、Claudeの`inherit`接続も会話contextを除いた通常の認証・接続設定を使う。
起動元ツールのエフェメラル仮想環境は`VIRTUAL_ENV`と`PATH`のコマンド格納ディレクトリの両方を取り除く。
`VIRTUAL_ENV`だけでは委譲先の`python`・コンソールスクリプトが起動元ツールの環境へ解決されるためである。
判断理由はClaude Agent SDKの`ClaudeAgentOptions.env`が継承環境へ重なる仕様でキーの削除を表現できず、
backend側で個別に取り除く案が成立しないことにある。
同じ契約を`atk`のprocess-loopも必要とするため、
契約本文は共有モジュール`agent-toolkit/agent_toolkit/_common/inherited_venv.py`だけに置く。
却下した代替案は、backendごとに除去処理を複製する案と、委譲先の各コマンド呼び出しで回避する案である。
前者は同じ契約が複数箇所へ分かれ、後者は回避策を規範や委譲プロンプトへ書き足す恒常費が残る。

Codexはstdio MCPサーバーへ親環境を限定して渡すため、認証・接続先・モデル設定と一般環境は`mcp_servers.<name>.env_vars`へ具体名を指定して継承する。
2026年10月13日に、Codexの委譲先から起動したCodexがproxy変数を失い`workspace routing discovery failed`で終了した。
静的な継承名と除外条件は`agent_toolkit/_common/mcp_environment.py`が持つ。
Codex向けプラグイン設定の生成器は、Anthropic・Claude、OpenAI・Codex、Google・Antigravity、AWS・Azureの認証と公開設定、proxy・CA・ロケール・保存先・ツール設定の具体名を`agents_server`と`pyfltr`へ同じ定義から射影する。
生成器は共通の静的名からホストによらず同じ配布設定を作成する。
Codex backendの開始・通常再開・保存済みthread再開では、App Serverへ渡す親環境に存在する全名前から除外名を取り除き、内側MCPの`env_vars`を生成する。
動的に列挙すると、事前登録のない独自名も保持できる。
既存の`proxy_environment.py`は同じproxy名のtupleを再公開する。
公開設定の具体名は`_atk/config.py`の`config_environment_variable_names`から引数で受け取る。
生成する箇所ごとに名前を書くと、片方だけの追加で通信の前提が消費側まで届かなくなるためである。
値は親環境にある場合だけCodexの継承機能へ任せ、配布設定へ保存しない。
同じ境界でXDGなどの保存先と公開設定overrideを失うと、子の登録先と親CLIの読取先が分かれ、起動済みのsessionを見付けられない。
通常起動・再開・多段でも呼出元が選んだ設定、キューと状態の名前空間を保つ。
除外は大文字小文字を区別せず、親会話・端末・プロセスの識別子と通知先、親shellの内部状態、上流の継承禁止名へ適用する。
認証名のTOKEN・KEYという字面は除外理由にしない。
ownerと状態の書込主体は各起動が明示した値を優先する。
静的境界ですでに失われた未知名は、内側の動的列挙でも回復できない。
子での別名前空間の全探索、親プロセスや認証ファイルの読直し、保存先のHOMEへの固定は採らない。
miseの設定・信頼状態・導入先・キャッシュの所在は`MISE_CONFIG_DIR`・`MISE_STATE_DIR`・`MISE_DATA_DIR`・`MISE_CACHE_DIR`の公開名で引き継ぎ、XDGを変更しても呼出元が選んだmiseの所在と信頼設定を保つ。
Claude向けとAgent Plugins向けのMCP定義へはCodex専用の`env_vars`を混ぜない。
agents_serverが起動するCodex App Server・Claude CLI・Antigravity CLIは会話contextを除いた環境を継承し、各backendは明示した起動別の値を加える。
Claude Codeのstdio環境の配送とremote変数による識別子の重複は、[audit-records.md](audit-records.md)の次の節にある。
参照先は「.claude/skills/agent-toolkit-edit/references/agents-server-shared-state-backends.md：バックエンド固有の接続とAPI状態：2026年10月18日」で、実MCPでの除去前後の観測も記録する。
却下した代替案は、chezmoiのCodexプラグイン方針へ置く案と、子プロセスがshell設定などから値を回復する案である。
前者の設定型は通信設定を受理せず、後者はCodexの公開機能で同じ結果を得られる。

atk自身のuv同期先は呼出元の`UV_PROJECT_ENVIRONMENT`から分離し、子へ渡す元の値と未設定の別を起動処理で保持する。
CLI・hook・MCP・warmup・process-loop再起動・生成定義はこの起動境界を共有する。
子環境では元の指定を復元し、既存のplugin環境の`VIRTUAL_ENV`とPATH要素の除去も適用する。
`UV_PYTHON`や`UV_CONFIG_FILE`など、同期先指定と別の公開設定は保持する。
変数の一律削除は子の環境選択を失わせるため採らない。
