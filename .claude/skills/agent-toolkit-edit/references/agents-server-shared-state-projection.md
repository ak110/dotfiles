# agents_serverの共有状態と読み書きの担当：索引と状態表示

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 共有状態の共通設計・実行主体・保存と回収の条件を判断する前 | `agents-server-shared-state.md` |
| セッション状態と内部フラグの参照範囲を選ぶ前 | `session-state-and-flags.md` |

## 索引と状態表示

| 共有状態 | 基準となる保持先 | 読む主体 | 更新できる主体 |
| --- | --- | --- | --- |
| statusline・CLI向けの状態ファイルと、書いたプロセスの識別`writer_process` | `<状態ディレクトリ>/<ルートsession識別子>/<書込主体>.json`（起動時に同名ファイルを別の生存プロセスが使用していれば`<書込主体>-p<pid>.json`） | statusline、`atk agents wait`、`atk agents list`、`atk agents show`、同一ルートに属する他のMCPサーバー（稼働中の子孫を持つ終了セッションの表示判定時）、および全ルートを参照するリソース監視処理 | そのルートに属する各MCPサーバー。各MCPサーバーは自身が担当するファイルのみを更新する |
| 書込主体からホストsessionへの索引 | `<状態ディレクトリ>/<ルートsession識別子>/hosts/<書込主体>.json` | 状態ファイル内の`host_session_id`を委譲元のセッション識別子へ解決する処理、PostToolUseフック、`atk agents wait` | そのセッションを起動したMCPサーバー |
| 状態ファイルの`host_session_id`（そのファイルが載せるsessionの委譲元） | 状態ファイル | statuslineおよび`atk agents list`のツリー表示（親子の関連付けにおける親IDとして利用。`status_reader.load_root`が各セッションに付与する）、同一ルートに属する他のMCPサーバー（稼働中の子孫プロセスの判定） | その状態ファイルを書き込むMCPサーバー。なお、状態ファイルのファイル名（`owner_status_file`）は書込主体の識別子であり、結果の所有権判定にのみ用い、親IDとして転用することはない |
| 状態ファイルの生存の印`heartbeat_at` | 状態ファイル内 | statusline、同一ルートに属する他のMCPサーバー | その状態ファイルを書き込むMCPサーバー |
| ルートsession識別子の索引 | `<状態ディレクトリ>/aliases/<現行のsession識別子>.json` | statusline、`atk agents wait`、`atk agents list`、`atk agents show` | PostToolUseフック（レスポンスの`root_session_id`を保存）、MCPの要求単位の初期化（`_meta.threadId`に対応するrootを公開後に保存） |

| 対象 | モジュール |
| --- | --- |
| ルートおよび書込主体の識別、索引管理（`aliases`・`hosts`） | `shared_roots.py` |
| statuslineおよびCLI向け状態ファイルの出力 | `status_file.py` |
| 一覧・リソース記録の共有読み取り、生存確認、セッションの重複排除 | `status_reader.py` |

子環境へ伝達するルートセッション識別子には、Managerが保持する`StatusFileWriter.root_session_id`を使用する。
環境変数にownerが存在せずManagerが自動生成したルート識別子であっても、Codex App Serverの子環境、thread開始・再開時のMCP環境とhosts索引、Claude SDKの新規・再開環境、およびAntigravityの子環境に対して共通の入力として渡される。
各バックエンドはこの入力情報に基づいて通知先の所有者を決定する。
Antigravity環境ではルート識別子の伝達のみを保証しており、固有の通知元識別子の解決や通知の成功可否は個別の課題として扱う。
計画の所有記録やUWIの投入元を解決するルールは本伝達の契約とは異なり、Managerが生成したプロセス専用のルート（`mcp-`で始まる識別子）は特定の会話を表すものではないため、所有会話や投入元の記録対象から除外される。
この判定ロジックは`agent-toolkit/agent_toolkit/_plan/owner_records.py`の`resolve_conversation_session_id`が管理している。
CodexのPostToolUseフックは、所有セッション識別子が存在する一方で環境変数から書込主体を解決できない場合、入力JSONに含まれる検証済みの現在セッション識別子を`AGENT_TOOLKIT_STATUS_HOST_SESSION`相当の値として補完する。
PostToolUseフックと`atk agents wait`は、`hosts`索引が存在していれば委譲元のthreadから書込主体を逆引きし、状態ファイルと待機対象登録を同一の名前空間で管理する。
索引が存在しない場合は、補完した識別子をそのまま書込主体として扱う。

委譲セッションのコンテキストトークン使用量（`context_tokens`および`context_window`）についても、MCPサーバーの`SessionState`を基準とし、同一の書込主体が状態ファイルへ任意の数値として反映する。
この値は累計の消費量ではなく、直近のモデルコンテキストの観測値であり、対象セッションのエンジンを所有するバックエンドが`_agents_server/context_usage.py`の`record_context_usage`を用いて更新する。
情報源はエンジンごとに以下の通りである。

- Claude: SDKの`get_context_usage()`から取得する`totalTokens`および`rawMaxTokens`
- Codex: 同一thread・ターンの`thread/tokenUsage/updated`イベントにおける`last.totalTokens`および`modelContextWindow`
- Antigravity: 最新の`agent_response`ステップにおける`total_tokens`と`cache_read_tokens`の合計（上限値は保持しない）

有限の非負の数値のみを受け付け、不明な値は項目ごと省略する。
この更新処理も最終アクティビティ時刻を進めることはなく、表示更新通知のみを発行する。
終了後の再開情報（`SessionResumeState`）には、表示用途として最後の観測値が保持され、同一セッションの表示にのみ使用される。
statuslineと`atk agents list`では、`subagent.rs`の`format_tokens`と`context_usage.format_tokens`を用いて統一した書式（小数第1位までの`k`表記と整数パーセント表示）で描画する。
Rustのユニットテストおよびリポジトリルートの`claude_statusline_invariant_test.py`は、`rust/claude-statusline/testdata/agents_server_state.json`にある同一の数値例を検証に使用する。

内部で保持するデータ項目と、公開APIのレスポンスに含める項目は明確に分離して扱う。
アクティビティ情報としては`seconds_since_activity`、API失敗情報としてはエラー種別・HTTPステータスコード・経過時間（利用上限の解除待ち中であれば制限種別と解除予定時刻も含む）を公開し、内部管理用の集計回数や初回到達時刻は公開レスポンスには含めない。
CLIの一覧表示では、識別子、状態、ラベル、直近の操作、保留状態、開始時刻、コンテキスト使用量、およびアクティビティ診断に必要な項目のみに限定して出力する。
MCPの通常レスポンスには絶対時刻を直接含めず、`show(verbose=True)`が指定された場合にのみ更新・出力時刻や実行条件を出力する。
結果のターン番号、確定時刻、通知送信時刻は内部保存とソート用に保持し、外部へ返す公開レスポンスからは除外する。
このように用途別の射影（Projection）を徹底することで内部管理項目の意図しない流出を防ぎ、所有者確認や安全な退避には既存のデータ構造を利用する。

索引ファイルを参照するのは、現在のセッション識別子からルートセッション識別子を特定する必要があるコンポーネントのみである。
MCPサーバーは、`start`、`send_message`、`list`のレスポンスにおいて、自身の状態ファイルの書き込み先となる`root_session_id`を明示して返す。
`list`はセッション一覧が空であっても同様にこの項目を含める。
PostToolUseフックは、レスポンス内に`root_session_id`が含まれていれば操作名に関わらずその値を索引へ直接書き込み、子セッション識別子から状態ディレクトリを逆引きすることはしない。
これは、`claude --resume`で起動した会話ではMCPサーバーと会話側の識別子が異なり、最初のMCP操作が`send_message`になる可能性があるためである。
`atk agents list`および`atk agents wait`は、索引または現在のセッション識別子自身の状態ディレクトリを参照して、ルートセッションとの対応関係を確認する。
対応が確認できず対象セッションが0件の場合は、CLIが特定したルート識別子を提示し、MCPの`list`を1回実行してから同じCLIを再実行するよう復旧手順を案内する。
対応関係の確認が完了している状態での0件は、通常の空状態として処理する。
索引のキーとなる現在のセッション識別子は、所有者セッションがあればその値、無ければ`_common/delegated_session.py`の`current_conversation_session_id`（`CLAUDE_CODE_SESSION_ID`、`CODEX_THREAD_ID`の順）とする。
ownerを持たないCodex最上位の会話は、索引が確認済みのrootを指す場合に、その`root.json`を待機対象として読む。
MCPが要求から会話を識別した場合は会話IDのroot、識別できない場合はプロセスごとのrootを使う。
索引が無い場合の`atk agents wait`は、MCPの`list`応答の`root_session_id`を`--root-session-id`へ渡す回復操作を案内する。

各MCPサーバーは、自身が所有する状態ファイルおよび関連する一時ファイルに加えて、ハートビート（生存シグナル）が失効した他の状態ファイルを削除できる。
ハートビート情報を持たない状態ファイルについては、勝手に削除せず保持する。
起動時に同名の状態ファイルのハートビートが有効であり、かつ`writer_process`で示されるプロセスが自プロセス以外としてOS上に存在する場合は、そのファイルを削除せずに自身は`<書込主体>-p<pid>.json`という個別ファイル名へ書き込む。
同一の会話内で起動インスタンスが交代した際に古いインスタンスが残存している場合、ファイルを削除してしまうと古いインスタンスのセッションを管理できる所有者が存在しないと誤認されるのを防ぐためである。
ハートビートは、状態ファイルを書き込んでいるインスタンスが現在応答可能であることのみを示す指標である。
セッションの終了や所有権の判定は、登録簿に記録されたプロセスの実在性を第一の根拠とする。
