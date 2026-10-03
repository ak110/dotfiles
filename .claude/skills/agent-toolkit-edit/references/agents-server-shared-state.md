# agents_serverの共有状態と読み書きの担当

`agents_server`の状態はMCPサーバーのメモリー、状態ディレクトリのファイル、フックが記録するセッション状態およびstatuslineが読む射影の4つの表現に分かれる。
実行主体ごとに更新できる範囲が異なるため、挙動を確定する際は関係する全ての処理の流れを読む。単一の処理だけを読むと、別の処理が同じ状態を更新しない事実を見逃す。
`agent-toolkit/agent_toolkit/agents_server_mcp.py`と`agent-toolkit/agent_toolkit/_agents_server/`配下を変更または調査する主体は、着手前に本書を読む。
`rust/claude-statusline/src/agents_server.rs`を扱う主体も同じとする。

## 実行主体

| 実行主体 | 実体 | 寿命 |
| --- | --- | --- |
| MCPサーバー | `agent-toolkit/agent_toolkit/agents_server_mcp.py` | ホストがMCPサーバーを起動する単位ごとに1プロセス。起動時の`CLAUDE_CODE_SESSION_ID`を保持し続ける。各プロセスが保持するsessionの集合は独立する |
| `atk`のCLI | `atk agents wait`、`atk agents notify`、`atk agents list`、`atk agents show`、`atk agents logs` | 呼び出しごとの短命プロセス。現行のsession識別子を得る |
| フック | `agent-toolkit/agent_toolkit/_hooks/posttooluse.py` | イベントごとの短命プロセス。入力JSONで現行のsession識別子を得る |
| statusline | `rust/claude-statusline` | 描画ごとの短命プロセス。入力JSONで現行のsession識別子を得る |

`CLAUDE_CODE_SESSION_ID`は子プロセスの起動時に現行のsession識別子が注入される値である。
Claude Codeが同一プロセスのままsession識別子を切り替えた場合、切替後に起動する短命プロセスは新しい値を得る一方、長命なMCPサーバーは起動時の値を保持し続ける。
MCPサーバープロセスには`CLAUDE_PID`が渡らないため、Claude Codeプロセスを指す識別子を環境変数から解決できない。

Codex CLIが起動するMCPサーバープロセスが受け取る環境変数は、外側の`agents_server`が`thread/start`の`config.mcp_servers.agents_server.env`で明示した値だけである。`codex app-server`自身の環境はこのプロセスへ継承されず、明示した値だけが届く。
ルートsession識別子と書込主体識別子は、この環境変数を介して配送する。
配送するルートはManagerが持つ`StatusFileWriter.root_session_id`である。環境にownerが無くManagerが生成したルートも、Codex App Serverの子環境、threadの開始・再開のMCP環境とhost索引、Claude SDKの新規・再開環境、Antigravityの子環境へ同じ入力として渡す。backendはこの入力から通知先の所有者を決める。Antigravityではルート配送だけを保証し、固有の通知元識別子の解決と通知成功は別の課題として扱う。計画の所有記録の解決はこの配送と別の契約である。
CodexのPostToolUseフックは、所有session識別子があり環境変数から書込主体を解決できない場合、入力JSONの検証済み現行session識別子を`AGENT_TOOLKIT_STATUS_HOST_SESSION`相当として補完する。PostToolUseフックと`atk agents wait`は`hosts`索引が存在する場合は委譲元のthreadから書込主体を逆引きし、状態ファイルと待機対象登録を同じ名前空間で扱う。索引が無い場合は補完した識別子をそのまま書込主体として使う。
Codex backendは、子sessionを起動した委譲先から`atk agents wait`に成功した`commandExecution`を受け取る。
標準出力、または`--output-file`の指定時と長い結果の自動保存時に保存先の行が示す保存結果の終端識別子を、`state.py`の`consume_agents_wait_output`で追跡集合から外す。
待機失敗と読取不能の結果は追跡集合へ残す。

## 共有状態ごとの保持先と読み書きの担当

| 共有状態 | 基準となる保持先 | 読む主体 | 更新できる主体 |
| --- | --- | --- | --- |
| session一覧と`status`・`progress` | MCPサーバーのメモリーの`SessionState` | MCPサーバー | MCPサーバーだけ |
| statusline・CLI向けの状態ファイル | `<状態ディレクトリ>/<ルートsession識別子>/<書込主体>.json` | statusline、`atk agents wait`、`atk agents list`、`atk agents show`、同じルートに属する他のMCPサーバー（稼働中の子孫を持つ終端sessionの表示判定） | そのルートに属する各MCPサーバー。各MCPサーバーは自身のファイルだけを書く |
| 書込主体からホストsessionへの索引 | `<状態ディレクトリ>/<ルートsession識別子>/hosts/<書込主体>.json` | 状態ファイルの`host_session_id`を委譲元のsession識別子へ解決する主体、PostToolUseフック、`atk agents wait` | そのsessionを起動したMCPサーバー |
| 状態ファイルの生存の印`heartbeat_at` | 状態ファイルを書き込むMCPサーバー | statusline、同じルートに属する他のMCPサーバー | その状態ファイルを書き込むMCPサーバー |
| 終端結果と回収済み判定 | `<状態ディレクトリ>/<ルートsession識別子>/results/<session_id>.json`の存在。CLIが回収途中の結果は下記のrun別退避物へ移る | MCPサーバー、`atk agents wait`、statusline | MCPサーバー（作成と削除）、待機CLI（自身の書込主体が公開した結果だけを削除） |
| CLI待機の所有権 | `<状態ディレクトリ>/<ルートsession識別子>/wait-locks/<書込主体>.lock`のファイルロック | `atk agents wait`、Stop時の未観測作業の助言 | `atk agents wait`。1書込主体につき同時に1実行だけが全対象を待ち、ロックファイル自体は解放後も保持する |
| CLI待機の実行結果 | `<状態ディレクトリ>/<ルートsession識別子>/wait-results/<書込主体>/<run_id>.json`と`current.json` | 先行waitへ合流する後続`atk agents wait` | 先行waitが`running`と`published`および再待機が必要かを示す`continuable`を書き、結果を返した後続waitが`consumed`を書く |
| CLI待機が回収途中の結果と通知 | `<状態ディレクトリ>/<ルートsession識別子>/wait-results/<書込主体>/<run_id>/results/<session_id>.json`と`notices/<通知ファイル>` | 中断後に同じrunを再開する`atk agents wait` | 待機CLI。結果は所有者を確認した排他区間で、通知は原本の削除前に退避する |
| CLI待機の対象登録 | `<状態ディレクトリ>/<ルートsession識別子>/wait-targets/<書込主体>/<session_id>.json` | `atk agents wait`、Stop時の未観測作業の助言 | PostToolUseフック（子sessionの開始とreply再開時の追加。`atk agents wait`と同じく索引を経たルートへ登録する）、`atk agents wait`（待機開始時の追加と回収・破棄時の削除）。助言側は読むだけとする |
| 全sessionの終端登録と再開情報、所有側による解放済みの理由と時刻、作成時点の委譲元（`launcher_session_id`） | `<状態ディレクトリ>/sessions/<session_id>.json` | 親を所有するMCPサーバー、同じ識別子を再解決するMCPサーバー、保持しない識別子への応答を診断するMCPサーバー、`atk agents wait`、`atk serve`のセッション一覧（ローカルとリモートヘルパー。委譲元だけを親子付けに読む） | そのsessionを所有するMCPサーバー（停止時の終端公開を含む）。所有者のいない`running`のCodexの記録への終端の公開だけは、再起動後に同じ識別子を照会したMCPサーバーも行う（下記「再起動をまたぐsessionの解決」の条件） |
| Codexコンパクションの計測記録 | `<状態ディレクトリ>/compaction/<thread_id>.jsonl` | `atk run-script session-review-evidence` | agents_serverのCodex backend |
| 上り通知 | `<状態ディレクトリ>/<ルートsession識別子>/notices/<通知ファイル>` | MCPサーバー、`atk agents wait` | `atk agents notify`が作成し、MCP待機とCLI待機が回収時に削除する |
| ルートsession識別子の索引 | `<状態ディレクトリ>/aliases/<現行のsession識別子>.json` | statusline、`atk agents wait`、`atk agents list`、`atk agents show` | PostToolUseフック（`start`、`send_message`、`list`など、`root_session_id`を明示する応答の値を操作名によらず使う） |
| MCPツールの呼び出し記録 | セッション状態の`agents_server_sessions` | PostToolUseフックとStop時の助言 | PostToolUseフック |
| 委譲先CLI自身の診断ログ | `<診断ログのディレクトリ>/delegate-debug/<起動時刻>-<session識別子>-<起動区分>.log` | 初期化失敗を事後に調べる主体 | Claude backend（作成、初期化完了後の改名と、保持世代を超えた記録の削除） |
| Antigravityの公開イベントログ | `<状態ディレクトリ>/<ルートsession識別子>/logs/<session_id>.jsonl` | `atk agents logs`、`atk run-script session-review-evidence`、セッションのイベント経過を調べる主体 | Antigravity backend（公開stream-jsonイベントの追記） |
| engineの可用性を理由に除外した候補 | `<状態ディレクトリ>/unavailable-candidates.json` | 起動の候補列を解決するMCPサーバー | その状態ディレクトリを共有する各MCPサーバー（ファイルロック下の読み書き） |

sessionの`created_at`は最初の開始時刻で、turnごとに更新する`started_at`と別に保持する。MCPサーバーのメモリーを基準とし、状態ファイルとsession登録簿へ射影する。再開したsessionは登録簿または退避した再開情報の値を引き継ぐ。項目を持たない旧形式の登録簿から再開した場合は再開時刻から数え直す。

ClaudeのAPI失敗が連続する間と、Codexの過負荷（`serverOverloaded`）による自動継続を待つ間の`api_error`は、いずれもMCPサーバーの`SessionState`を正とし、同じ書込主体が状態ファイルへ射影する。過負荷の待機中の記録は待機を予定したCodex backendの終端処理が作成し、MCPサーバーが継続のturnを送った時点で削除する。待機の予定時刻と連鎖の回数もMCPサーバーのメモリーだけに置き、状態ファイルへは`api_error`の形でだけ射影する。API失敗の合成メッセージは活動時刻を進めず、状態ファイルの更新だけを通知する。`show`・`list`とCLIは共通の活動射影から失敗の経過を算出し、正常なassistantメッセージで記録を削除する。生のエラー本文は共有状態へ保存しない。

状態ディレクトリは`atk config get state_dir`が返すディレクトリ配下の`agents-server`とする。
内部の保存項目と公開応答の項目は別に扱う。活動は`seconds_since_activity`、API失敗は種別・HTTP状態・経過時間を公開し、集計回数と初回時刻は内部の保持に使う。CLI一覧は識別子・状態・ラベル・直近操作・結果保留・開始時刻と活動診断へ限定する。MCPの通常応答には絶対活動時刻を載せず、`show(verbose=True)`で更新・出力時刻と実行条件を示す。結果のturn番号と確定時刻、通知の送信時刻は保存と整列に残し、回収する公開応答から除く。用途別の射影で内部追加の流入を防ぎ、所有者確認と耐久退避は従来の入力を使う。
診断ログのディレクトリは`agents-server.log`を置く階層とし、`agent-toolkit/agent_toolkit/_agents_server/logging_config.py`の`state_dir`が解決する。
Antigravityのイベント処理を調査するときは、上表の公開イベントログを参照する。書き込みに失敗した場合はbackendの警告ログを参照し、イベント処理の終端状態はsession状態から判定する。

索引を読むのは、現行のsession識別子からルートsession識別子を解決する主体だけである。
MCPサーバーは`start`、`send_message`、`list`の応答へ、自身の状態ファイル書込先である`root_session_id`を明示する。`list`はsession一覧が空でも同じ項目を返す。PostToolUseフックは`root_session_id`を持つ応答であれば操作名によらずその項目を索引へ直接書き、子session識別子から状態ディレクトリを逆引きしない。`claude --resume`で起動した会話でもMCPサーバーと会話側の識別子が分かれ、最初のMCP操作が`send_message`になり得るためである。
`atk agents list`と`atk agents wait`は索引または現行識別子自身の状態ディレクトリからルートsessionとの対応を確認する。対応を確認できず対象が0件の場合は、CLIが解決したrootを示し、MCPの`list`を1回呼んで同じCLIを再実行する復旧手順を返す。対応確認済みの空状態は通常の空状態として扱う。
`atk agents notify`は委譲先から`AGENT_TOOLKIT_OWNER_SESSION`で所有者sessionを直接解決するため、索引の読み取りを省く。
子から親へ通知する場合は宛先を所有者sessionから解決する。現行のsession識別子から解決すると、宛先が自分自身になるためである。

## 判定を確定する前に確認すること

- 対象の状態について、上表の「更新できる主体」が2つ以上あるかを確認する。2つ以上ある場合は、全ての更新主体による処理の流れを読んで網羅を判定する。
- MCPサーバーのメモリーにだけ存在する状態を更新できるのはMCPサーバーだけであり、プロセス境界の外にある`atk`のCLIとフックは更新する主体から外れる。その状態を判定に用いる処理が、CLI経由の操作でも成立するかを個別に確認する。
- 新しい状態は1つの表現で保持する形へ設計する。未回収の終端結果は、そのファイルを書いた主体の公開台帳と結果ファイルの在否から`published`、`consumed`、`unpublished`へ区分する。CLIが回収途中の結果はrun別退避物を再開時の基準とし、原本が残る中断点では同じ本文を重ねて配送しない。`SessionState.result_delivered`はファイルを削除する契機を表す内部状態であり、ファイル表現を持たない処理に限り用いる。
- MCP待機とCLI待機は`status_file.take_result`で所有者の確認、排他取得、本文の読取および削除を1つの区間として実行する。CLI待機は同じ排他区間でrun別退避物を確定してから原本を削除する。MCP待機は退避先を渡さず、従来の回収結果を使う。
- 待機所有権と待機対象の登録は、いずれも書込主体を単位とする名前空間で表す。読む側も同じ単位で読み、探す対象は書込主体を名前とするロックと登録とする。
- `atk agents wait`の前景実行と背景実行は、書込主体ごとに同時に1つとする。ロック競合を観測した後発は`current.json`が示すrun IDを固定し、同じロックの取得後に固定runの状態を読み直す。`published`か`foreground-delivered`なら保存本文と終了コードを返し、返却後に`consumed`へ遷移させて結果の再演を1回に限る。`running`なら保存対象とrun別退避物の対象を引き継ぎ、lock待ち中の追加対象も含めて同じrunの待機を再開する。競合せずロック取得に成功した後続waitも、現在の対象集合が残存する`running` runと一致すれば退避物を引き継いで待機を再開する。原本の回収後にrun別退避物だけへ残るsessionも対象集合の一致確認へ含める。対象集合が一致し、`continuable`がfalseの`published` runは結果を1回返す。対象集合が変わったrunと継続可能な公開runは新規待機へ進む。`consumed`のrunは再利用せず、新規runを開始する。待機上限への到達または通知だけを返したrunは`continuable`をtrueとし、次の発行は新規runを開始する。run記録がない旧形式のlockだけを観測した場合は、旧形式との競合を示す診断で終了する。ロックの単位は書込主体とし、対象sessionごとのロックは除外する。

CLI待機の配送は結果と通知の損失防止を優先する。runへ本文を保存してからstdoutまたはstderrへflushし、その後に配送済みのrun状態を保存する。後続waitが保存済みの本文を配送する場合も、flush後に`consumed`を保存する。どちらの場合でも、flush後かつ配送状態の保存前にプロセスが停止すると、次のwaitは保存本文を1回再出力してよい。強制終了を伴わない同時発行の合流では同じrunを1回だけ回収し、1回の再開処理で同じsessionの結果を重ねて出力しない。flushと状態ファイルの更新を受信確認の仕組みなしに原子化できないため、この中断点での再出力を許容する。

各MCPサーバーは、自身が所有する状態ファイルと対応する一時ファイルに加え、生存の印が失効した他の状態ファイルを削除できる。生存の印を持たない状態ファイルは保持する。`results`配下は共有するため、削除できるのは結果本文を委譲元へ配送した後、CLIのrun別退避物へ耐久的に保存した後、または`stop`による明示的な破棄の後とする。削除の契機はこれらに限り、経過時間は契機から外す。`notices`および`hosts`配下も共有するため、各書込主体が削除できるのは保持期限を超えたファイルだけとする。`sessions`配下の登録簿レコードを解放済みレコード（理由と時刻だけを持ち、再開条件を持たない）へ置き換えられるのはそのsessionを所有するMCPサーバーだけとし、契機は`stop`による明示的な破棄と保持期限の経過に限る。終端を観測した待機側はそのレコードを保持する。レコードを削除せず置き換えるのは、保持しない識別子を照会した別のMCPサーバーが、所有側による解放と記録の不在を区別して応答するためである。委譲元の項目は作成時に書き、状態の更新と解放済みへの置き換えでも既存の値を引き継ぐ。

MCPサーバーの起動時とSessionEndでは、配下の全ファイルの最終更新から7日を超えたrootディレクトリ、`sessions`の登録簿、および`compaction`の計測記録を掃引する。現在の会話のrootは除外し、`compaction`のJSONLとlockは対として削除する。個別の削除失敗を記録し、後続対象の掃引と起動・終了処理を継続する。

- 再起動をまたぐsessionの解決はsession登録簿に従う。statusline向け状態ファイルは書込主体を解決できる処理だけで作成されるため、解決の入力から外す。登録簿が終端を示さないsessionは、別プロセスがturnを実行している可能性を排除できないため再開しない。所有側は停止時に、backendの停止で終わった未終端のturnを`interrupted`として登録簿と結果ファイルへ公開し、記録を終端へ進める。それでも`running`のまま残ったCodexの記録は、照会したMCPサーバーが次の2条件をともに確かめた場合だけ終端を公開して復元へ進む。生存の印が有効な状態ファイルがどれもそのsessionを載せていないことと、自身のCodex App Serverへの`thread/read`が登録簿の`turn_id`のturn（項目の無い記録では全turn）の終端を示し、後続に進行中のturnが無いことである。引き継ぐ側が行うのは終端の公開だけとし、解放済みへの置き換えは所有側に残す。
- 登録簿のレコードが不在、解放済みまたは読取不能である場合は、同じ識別子の終端結果ファイルを確認し、そのファイルが終端を示す場合は終端として扱う。解放済みと不在は所有側の解放と7日超の掃引のいずれからも生じ、終端結果の在否と独立するため、終端結果の有無の判定には結果ファイルを用いる。解放済みはそれ以外の判定（復元、待機対象の解放、実行中の判定）でも不在と同じに扱う。

## 本書の更新が必要になる変更

- 上表のいずれかの状態について、基準となる保持先、読む主体または更新できる主体を変える変更。
- 状態ディレクトリ配下へ新しい種類のファイルを置く変更。
- MCPサーバー、`atk`のCLI、フックおよびstatuslineのいずれかへ、既存の共有状態を読み書きする処理を追加する変更。
