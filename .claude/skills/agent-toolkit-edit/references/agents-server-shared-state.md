# agents_serverの共有状態と読み書き経路

`agents_server`の状態は、MCPサーバーのメモリー、状態ディレクトリのファイル、フックが記録するセッション状態及びstatuslineが読む射影の4つの表現に分かれる。
実行主体ごとに更新できる範囲が異なるため、1つの経路だけを読んで挙動を確定すると、別の経路が同じ状態を更新しない事実を見落とす。
`agent-toolkit/agent_toolkit/agents_server_mcp.py`と`agent-toolkit/agent_toolkit/_agents_server/`配下を変更又は調査する主体は、着手前に本書を読む。
`rust/claude-statusline/src/agents_server.rs`を扱う主体も同じとする。

## 実行主体

| 実行主体 | 実体 | 寿命 |
| --- | --- | --- |
| MCPサーバー | `agent-toolkit/agent_toolkit/agents_server_mcp.py` | ホストがMCPサーバーを起動する単位ごとに1プロセス。起動時の`CLAUDE_CODE_SESSION_ID`を保持し続ける。各プロセスが保持するsessionの集合は独立する |
| `atk`のCLI | `atk agents wait`、`atk agents notify`、`atk agents list`、`atk agents show` | 呼び出しごとの短命プロセス。現行のsession識別子を得る |
| フック | `agent-toolkit/agent_toolkit/_hooks/posttooluse.py` | イベントごとの短命プロセス。入力JSONで現行のsession識別子を得る |
| statusline | `rust/claude-statusline` | 描画ごとの短命プロセス。入力JSONで現行のsession識別子を得る |

`CLAUDE_CODE_SESSION_ID`は子プロセスの起動時に現行のsession識別子が注入される値である。
Claude Codeが同一プロセスのままsession識別子を切り替えた場合、切替後に起動する短命プロセスは新しい値を得る一方、長命なMCPサーバーは起動時の値を保持し続ける。
MCPサーバープロセスには`CLAUDE_PID`が渡らないため、Claude Codeプロセスを指す識別子を環境変数から解決できない。

Codex CLIが起動するMCPサーバープロセスへは、`codex app-server`自身の環境が継承されない。
当該プロセスが受け取るのは、外側の`agents_server`が`thread/start`の`config.mcp_servers.agents_server.env`で明示した環境変数だけである。
ルートsession識別子と書込主体識別子は、この経路で配送する。

## 共有状態ごとの正本と読み書き経路

| 共有状態 | 正本 | 読む主体 | 更新できる主体 |
| --- | --- | --- | --- |
| session一覧と`status`・`progress` | MCPサーバーのメモリーの`SessionState` | MCPサーバー | MCPサーバーだけ |
| statusline・CLI向けの状態ファイル | `<状態ディレクトリ>/<ルートsession識別子>/<書込主体>.json` | statusline、`atk agents wait`、`atk agents list`、`atk agents show` | 当該ルートに属する各MCPサーバー |
| 書込主体からホストsessionへの索引 | `<状態ディレクトリ>/<ルートsession識別子>/hosts/<書込主体>.json` | 状態ファイルの`host_session_id`を起動元のsession識別子へ解決する主体 | 当該sessionを起動したMCPサーバー |
| 状態ファイルの生存の印`heartbeat_at` | 状態ファイルを書き込むMCPサーバー | statusline、同じルートに属する他のMCPサーバー | 当該状態ファイルを書き込むMCPサーバー |
| 終端結果と回収済み判定 | `<状態ディレクトリ>/<ルートsession識別子>/results/<session_id>.json`の存在 | MCPサーバー、`atk agents wait`、statusline | MCPサーバー（作成と削除）、待機CLI（自身の書込主体が公開した結果だけを削除） |
| CLI待機の所有権 | `<状態ディレクトリ>/<ルートsession識別子>/wait-locks/<書込主体>.lock`のファイルロック | `atk agents wait`、Stop時の未観測作業の助言 | `atk agents wait`。1書込主体につき同時に1実行だけが全対象を待ち、ロックファイル自体は解放後も保持する |
| CLI待機の対象登録 | `<状態ディレクトリ>/<ルートsession識別子>/wait-targets/<書込主体>/<session_id>.json` | `atk agents wait`、Stop時の未観測作業の助言 | `atk agents wait`（追加と削除）。助言側は読むだけで内容を変更しない |
| 全sessionの終端登録と再開情報 | `<状態ディレクトリ>/sessions/<session_id>.json` | 親を所有するMCPサーバー、同じ識別子を再解決するMCPサーバー | 当該sessionを所有するMCPサーバー |
| Codexコンパクションの計測記録 | `<状態ディレクトリ>/compaction/<thread_id>.jsonl` | session-reviewの証拠抽出器 | agents_serverのCodex backend |
| 上り通知 | `<状態ディレクトリ>/<ルートsession識別子>/notices/<通知ファイル>` | MCPサーバー、`atk agents wait` | `atk agents notify` |
| ルートsession識別子の索引 | `<状態ディレクトリ>/aliases/<現行のsession識別子>.json` | statusline、`atk agents wait`、`atk agents list`、`atk agents show` | PostToolUseフック（`start`系応答の`session_id`を共有状態ファイルへ照合する） |
| MCPツールの呼び出し記録 | セッション状態の`agents_server_sessions` | PostToolUseフックとStop時の助言 | PostToolUseフック |
| 委譲先CLI自身の診断記録 | `<診断ログのディレクトリ>/delegate-debug/<起動時刻>-<プロセスID>-<起動区分>.log` | 初期化失敗を事後に調べる主体 | Claude backend（作成と、保持世代を超えた記録の削除） |

状態ディレクトリは`atk config get state_dir`が返すディレクトリ配下の`agents-server`とする。
診断ログのディレクトリは`agents-server.log`を置く階層とし、`agent-toolkit/agent_toolkit/_agents_server/logging_config.py`の`state_dir`が解決する。

索引を読むのは、現行のsession識別子からルートsession識別子を解決する主体だけである。
MCPサーバーは`start`系の応答を返す前に起動したsessionを状態ファイルへ同期反映する。PostToolUseフックは応答の`session_id`を状態ファイルへ照合し、一意に得たルートsession識別子を索引へ書く。公開応答へ索引用の内部識別子を加えない。
`atk agents notify`は委譲先から`AGENT_TOOLKIT_OWNER_SESSION`で所有者sessionを直接解決するため、索引を読まない。
子から親へ通知する経路では所有者sessionが宛先の正本であり、現行のsession識別子から解決すると宛先が自分自身になるためである。

## 判定を確定する前に確認すること

- 対象の状態について、上表の「更新できる主体」が2つ以上あるかを確認する。2つ以上ある場合は、片方だけを読んで網羅を判定しない。
- MCPサーバーのメモリーにだけ存在する状態は、プロセス境界の外にある`atk`のCLIとフックからは更新できない。当該状態を判定へ用いる経路が、CLI経由の操作でも成立するかを個別に確認する。
- 同じ事実を2つの表現で保持する状態を新設しない。未回収の終端結果は、当該ファイルを書いた主体の公開台帳とファイルの在否から`published`、`consumed`、`unpublished`へ区分する。回収済みと判定するのは`consumed`だけであり、ファイルが不在であることだけを根拠にしない。`SessionState.result_delivered`はファイルを削除する契機を表す内部状態であり、ファイル表現を持たない経路に限り用いる。
- MCP待機とCLI待機は`status_file.take_result`で所有者照合、排他取得、本文の読取及び削除を1つの区間として実行する。個別の待機入口で結果ファイルを読んでから削除する処理を持たない。
- 待機所有権と待機対象の登録は、いずれも書込主体を単位とする名前空間で表す。読む側も同じ単位で読み、対象session識別子を名前とするロック又は登録を探さない。
- `atk agents wait`の前景実行と背景実行を同じ書込主体で重ねない。後発は書込主体、対象session群及び「先行待機の終了後に再実行する」という次の操作を含む診断で終了する。対象sessionごとのロックは、動的に増える対象間で一部だけ所有する状態を生じさせるため用いない。

各MCPサーバーは、自身が所有する状態ファイルと対応する一時ファイルに加え、生存の印が失効した他の状態ファイルを削除できる。生存の印を持たない状態ファイルは削除しない。`results`配下は共有するため、削除できるのは結果本文を呼び出し元へ配送した後と、`stop`による明示的な破棄の後だけとする。経過時間を条件とする削除は行わない。`notices`および`hosts`配下も共有するため、各書込主体が削除できるのは保持期限を超えたファイルだけとする。`sessions`配下の登録簿レコードを削除できるのは当該sessionを所有するMCPサーバーだけとし、削除の契機は`stop`による明示的な破棄と保持期限の経過に限る。終端を観測した待機側は当該レコードを削除しない。

- 再起動をまたぐsessionの解決はsession登録簿を正本とする。statusline向け状態ファイルは書込主体を解決できる経路でだけ作成されるため、解決の入力にしない。登録簿が終端を示さないsessionは、別プロセスがturnを実行している可能性を排除できないため再開しない。
- 登録簿のレコードが不在又は読取不能であることを、当該sessionが終端結果を残していない根拠にしない。同じ識別子の終端結果ファイルを照合し、当該ファイルが終端を示す場合は終端として扱う。レコードの不在は削除の契機と保持期限の経過のいずれからも生じ、終端結果の在否と独立するためである。

## 本書の更新が必要になる変更

- 上表のいずれかの状態について、正本の所在、読む主体又は更新できる主体を変える変更。
- 状態ディレクトリ配下へ新しい種類のファイルを置く変更。
- MCPサーバー、`atk`のCLI、フック及びstatuslineのいずれかへ、既存の共有状態を読み書きする経路を追加する変更。
