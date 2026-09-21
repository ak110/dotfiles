# agents_serverの共有状態と読み書き経路

`agents_server`の状態は、MCPサーバーのメモリー、状態ディレクトリのファイル、フックが記録するセッション状態及びstatuslineが読む射影の4つの表現に分かれる。
実行主体ごとに更新できる範囲が異なるため、挙動の確定には関係する全ての経路を読む。1つの経路だけの読み取りでは、別の経路が同じ状態を更新しない事実を見落とす。
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

Codex CLIが起動するMCPサーバープロセスが受け取る環境変数は、外側の`agents_server`が`thread/start`の`config.mcp_servers.agents_server.env`で明示した値だけである。`codex app-server`自身の環境はこのプロセスへ継承されず、明示した値だけが届く。
ルートsession識別子と書込主体識別子は、この経路で配送する。
CodexのPostToolUseフックは、所有session識別子があり環境変数から書込主体を解決できない場合、入力JSONの検証済み現行session識別子を`AGENT_TOOLKIT_STATUS_HOST_SESSION`相当として補完する。PostToolUseフックと`atk agents wait`は、`hosts`索引が存在する場合は起動元threadから書込主体を逆引きし、状態ファイルと待機対象登録を同じ名前空間で扱う。索引が無い場合は補完した識別子をそのまま書込主体として使う。

## 共有状態ごとの正本と読み書き経路

| 共有状態 | 正本 | 読む主体 | 更新できる主体 |
| --- | --- | --- | --- |
| session一覧と`status`・`progress` | MCPサーバーのメモリーの`SessionState` | MCPサーバー | MCPサーバーだけ |
| statusline・CLI向けの状態ファイル | `<状態ディレクトリ>/<ルートsession識別子>/<書込主体>.json` | statusline、`atk agents wait`、`atk agents list`、`atk agents show` | そのルートに属する各MCPサーバー |
| 書込主体からホストsessionへの索引 | `<状態ディレクトリ>/<ルートsession識別子>/hosts/<書込主体>.json` | 状態ファイルの`host_session_id`を起動元のsession識別子へ解決する主体、PostToolUseフック、`atk agents wait` | そのsessionを起動したMCPサーバー |
| 状態ファイルの生存の印`heartbeat_at` | 状態ファイルを書き込むMCPサーバー | statusline、同じルートに属する他のMCPサーバー | その状態ファイルを書き込むMCPサーバー |
| 終端結果と回収済み判定 | `<状態ディレクトリ>/<ルートsession識別子>/results/<session_id>.json`の存在 | MCPサーバー、`atk agents wait`、statusline | MCPサーバー（作成と削除）、待機CLI（自身の書込主体が公開した結果だけを削除） |
| CLI待機の所有権 | `<状態ディレクトリ>/<ルートsession識別子>/wait-locks/<書込主体>.lock`のファイルロック | `atk agents wait`、Stop時の未観測作業の助言 | `atk agents wait`。1書込主体につき同時に1実行だけが全対象を待ち、ロックファイル自体は解放後も保持する |
| CLI待機の実行結果 | `<状態ディレクトリ>/<ルートsession識別子>/wait-results/<書込主体>/<run_id>.json`と`current.json` | 先行waitへ合流する後続`atk agents wait` | 先行waitが`running`と`published`及び再待機が必要かを示す`continuable`を書き、結果を返した後続waitが`consumed`を書く |
| CLI待機の対象登録 | `<状態ディレクトリ>/<ルートsession識別子>/wait-targets/<書込主体>/<session_id>.json` | `atk agents wait`、Stop時の未観測作業の助言 | PostToolUseフック（子sessionの開始とreply再開時の追加）、`atk agents wait`（待機開始時の追加と回収・破棄時の削除）。助言側は読むだけとする |
| 全sessionの終端登録と再開情報 | `<状態ディレクトリ>/sessions/<session_id>.json` | 親を所有するMCPサーバー、同じ識別子を再解決するMCPサーバー | そのsessionを所有するMCPサーバー |
| Codexコンパクションの計測記録 | `<状態ディレクトリ>/compaction/<thread_id>.jsonl` | session-reviewの証拠抽出器 | agents_serverのCodex backend |
| 上り通知 | `<状態ディレクトリ>/<ルートsession識別子>/notices/<通知ファイル>` | MCPサーバー、`atk agents wait` | `atk agents notify` |
| ルートsession識別子の索引 | `<状態ディレクトリ>/aliases/<現行のsession識別子>.json` | statusline、`atk agents wait`、`atk agents list`、`atk agents show` | PostToolUseフック（`start`系と`list`の応答が明示する`root_session_id`を使う） |
| MCPツールの呼び出し記録 | セッション状態の`agents_server_sessions` | PostToolUseフックとStop時の助言 | PostToolUseフック |
| 委譲先CLI自身の診断記録 | `<診断ログのディレクトリ>/delegate-debug/<起動時刻>-<session識別子>-<起動区分>.log` | 初期化失敗を事後に調べる主体 | Claude backend（作成、初期化完了後の改名と、保持世代を超えた記録の削除） |
| engineの可用性を理由に除外した候補 | `<状態ディレクトリ>/unavailable-candidates.json` | 起動の候補列を解決するMCPサーバー | その状態ディレクトリを共有する各MCPサーバー（ファイルロック下の読み書き） |

状態ディレクトリは`atk config get state_dir`が返すディレクトリ配下の`agents-server`とする。
診断ログのディレクトリは`agents-server.log`を置く階層とし、`agent-toolkit/agent_toolkit/_agents_server/logging_config.py`の`state_dir`が解決する。

索引を読むのは、現行のsession識別子からルートsession識別子を解決する主体だけである。
MCPサーバーは`start`系と`list`の応答へ、自身の状態ファイル書込先である`root_session_id`を明示する。`list`はsession一覧が空でも同じ項目を返す。PostToolUseフックは応答の当該項目を索引へ直接書き、子session識別子から状態ディレクトリを逆引きしない。
`atk agents list`と`atk agents wait`は、索引又は現行識別子自身の状態ディレクトリから会話rootとの対応を確認する。対応を確認できず対象が0件の場合は、CLIが解決したrootを示し、MCPの`list`を1回呼んで同じCLIを再実行する復旧手順を返す。対応確認済みの空状態は通常の空状態として扱う。
`atk agents notify`は委譲先から`AGENT_TOOLKIT_OWNER_SESSION`で所有者sessionを直接解決するため、索引の読み取りを省く。
子から親へ通知する経路では所有者sessionが宛先の正本であり、現行のsession識別子から解決すると宛先が自分自身になるためである。

## 判定を確定する前に確認すること

- 対象の状態について、上表の「更新できる主体」が2つ以上あるかを確認する。2つ以上ある場合は、全ての更新主体の経路を読んで網羅を判定する。
- MCPサーバーのメモリーにだけ存在する状態は、プロセス境界の外にある`atk`のCLIとフックからは更新できない。その状態を判定へ用いる経路が、CLI経由の操作でも成立するかを個別に確認する。
- 新しい状態は1つの表現で保持する形へ設計する。未回収の終端結果は、そのファイルを書いた主体の公開台帳とファイルの在否から`published`、`consumed`、`unpublished`へ区分する。回収済みと判定するのは`consumed`だけとし、判定の入力には公開台帳とファイルの在否の両方を用いる。`SessionState.result_delivered`はファイルを削除する契機を表す内部状態であり、ファイル表現を持たない経路に限り用いる。
- MCP待機とCLI待機は`status_file.take_result`で所有者照合、排他取得、本文の読取及び削除を1つの区間として実行する。個別の待機入口は同じ関数を呼び、結果ファイルの読取と削除もその区間の内側で行う。
- 待機所有権と待機対象の登録は、いずれも書込主体を単位とする名前空間で表す。読む側も同じ単位で読み、探す対象は書込主体を名前とするロックと登録とする。
- `atk agents wait`の前景実行と背景実行は、書込主体ごとに同時に1つとする。ロック競合を観測した後発は`current.json`が示すrun IDを固定し、同じロックの解放後にそのrunの本文と終了コードを返す。返却後は`consumed`へ遷移させ、結果の再演は1回に限る。ロック取得後に開始する後続waitは、対象集合が一致し、`continuable`がfalseの`published`であればそのrunを返す。`consumed`のrunは再利用せず、新規runを開始する。待機上限への到達又は通知だけを返したrunは`continuable`をtrueとし、次の発行は新規runを開始する。run記録がない旧形式のlockだけを観測した場合は、旧形式との競合を示す診断で終了する。ロックの単位は書込主体とし、対象sessionごとのロックは除外する。

各MCPサーバーは、自身が所有する状態ファイルと対応する一時ファイルに加え、生存の印が失効した他の状態ファイルを削除できる。生存の印を持たない状態ファイルは保持する。`results`配下は共有するため、削除できるのは結果本文を呼び出し元へ配送した後と、`stop`による明示的な破棄の後だけとする。削除の契機はこの2つに限り、経過時間は契機から外す。`notices`および`hosts`配下も共有するため、各書込主体が削除できるのは保持期限を超えたファイルだけとする。`sessions`配下の登録簿レコードを削除できるのはそのsessionを所有するMCPサーバーだけとし、削除の契機は`stop`による明示的な破棄と保持期限の経過に限る。終端を観測した待機側はそのレコードを保持する。

MCPサーバーの起動時とSessionEndでは、配下の全ファイルの最終更新から7日を超えたrootディレクトリ、`sessions`の登録簿、及び`compaction`の計測記録を掃引する。現在の会話のrootは除外し、`compaction`のJSONLとlockは対として削除する。個別の削除失敗を記録し、後続対象の掃引と起動・終了処理を継続する。

- 再起動をまたぐsessionの解決はsession登録簿を正本とする。statusline向け状態ファイルは書込主体を解決できる経路でだけ作成されるため、解決の入力から外す。登録簿が終端を示さないsessionは、別プロセスがturnを実行している可能性を排除できないため再開しない。
- 登録簿のレコードが不在又は読取不能である場合は、同じ識別子の終端結果ファイルを照合し、そのファイルが終端を示す場合は終端として扱う。レコードの不在は削除の契機と保持期限の経過のいずれからも生じ、終端結果の在否と独立するため、終端結果の有無の判定には結果ファイルを用いる。

## 本書の更新が必要になる変更

- 上表のいずれかの状態について、正本の所在、読む主体又は更新できる主体を変える変更。
- 状態ディレクトリ配下へ新しい種類のファイルを置く変更。
- MCPサーバー、`atk`のCLI、フック及びstatuslineのいずれかへ、既存の共有状態を読み書きする経路を追加する変更。
