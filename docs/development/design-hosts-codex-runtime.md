# Codexの起動管理と更新jobの設計記録

[設計記録の索引](design.md)から参照する、Codexの起動管理と更新jobの記録である。

## Claude CodeとCodexの規範配置

### Codexの起動管理とdaemon復旧

通常TUIの起動管理は`pytools/codex_launcher.py`が所有し、`bin/codex`とWindowsのペアから導入済みの`dotfiles-codex`を起動する。
Codexの起動中は依存関係を導入せず、post-applyと同じpytools環境を使う。
公式standaloneの実行体は改変せず、Linuxの可視リンクとWindowsのPATH順を導入処理で調整する。
Windows公式インストーラーの可視binはpackageのbinへのjunctionであるため、その中へwrapperを書き込まず既存dotfiles/binを優先する。
実行時の`CODEX_HOME`は設定・認証・会話の保存先として保持し、導入した実行体の選択から分離する。
Linuxは可視コマンドの接続先をwrapperへ変更する前に、公式のcurrentへの参照を同じbinの`.dotfiles-codex-native`へ1個保持する。
更新時はこの参照を原子的に交換し、Windowsは可視binに残した公式実行体を直接使用する。
元の標準入出力とTTYはnativeへ継承し、公開`--remote`のloopback WebSocketと`app-server proxy`の間だけを中継する。
待受は起動ごとの認証トークンを必須とし、RPC本文は中継の寿命内のメモリーにだけ保持する。
`--remote`で接続したTUIは自身のcwdを送らず、管理daemonのcwdで会話を始める。
このため起動管理は、`-C`・`--cd`の無い対話起動へ起動したディレクトリを`--cd`として補い、通常起動と同じ作業ルートを保つ。
別の常駐サービスとTTY代理は設けない。

`plugin`・`mcp`・`exec`・`--version`・`completion`などの非対話コマンドは、hookからも繰り返し起動される。
起動管理はこれらを、復旧用の依存（psutil・pydantic・asyncio・中継モジュール）を読み込む前に同じ公式実行体へ引き渡す。
実行体の選択は標準ライブラリだけの`pytools/_internal/codex_runtime.py`が持ち、導入処理と起動管理が共用する。
非対話コマンドが復旧用の依存を読み込まないことは`pytools/codex_launcher_test.py`で試験する。

会話は実際の`thread/start`・`thread/resume`応答の正確なIDで選び、一時的なタイトル生成threadや同じcwdの別会話と区別する。
接続断またはdaemon世代の交代を観測すると、公開`initialize`・`thread/resume`・`thread/turns/list`で保存済み状態を確認する。
元turnが未完了で現在のwriterが無く、明示中止と新しいユーザーturnも無い場合だけ`turn/start`で未完了工程の継続を求める。
通常の会話開始・resumeとdaemon復旧の双方で、会話に属するagents_serverの`list`を公開MCP RPCから呼び、モデルの最初のツール要求へ依存せず共通の子孫復旧へ接続する。
保存済みIDの再開では、子MCPが起動時に直接親を確定できるよう、`thread/resume`の送信前にhost索引を公開する。
応答後に公開すると、子MCPが所有rootを直接親として孫の復旧候補を確定してしまう。
要求の応答を失っただけで副作用のある操作を再送せず、会話へ保存済みの状態を照合してから続ける。
応答を失ったRPC名と、元の依頼・実行履歴・操作対象の状態を同じ会話で確認する入力方法をTUIへ返す。

nativeの世代記録は`processIdentity`を省略可能な項目とし、Linux・macOS以外（Windows）では書かない。
世代は`pid`と`processIdentity`で識別し、`processIdentity`が無い記録は全OSで書かれる`processStartTime`で識別する。
daemon 0.166.0は世代ファイルを空の予約ファイルとして直接作成し、その後別inodeの完全なJSONへ交換する。
ファイルの存在を完成済みJSONの保証にせず、空本文だけを有限の未準備状態として扱う。
非空の不正JSONと期限を越えた予約状態は復旧失敗にする。
旧daemonの承認要求IDを中継側で分離し、再起動後に同じ数値IDが再利用されても旧応答で承認しない。
復旧用RPCがMCPの対話要求を待つ間も、TUIからの応答は現在の接続へ中継する。
旧要求は接続を閉じる前に無効化し、ユーザーの明示終了では対話待ちの復旧を中断して所有資源を回収する。
通常終了・明示中止・完了済みturnを復活させず、所有TUIがdaemon交代で終了した場合だけ同じIDと保存した条件で再起動する。
非対話の`codex exec`とagents_serverの専用App Serverは共有daemonに依存せず、その経路を置き換えない。

### Codexからの更新jobと結果の保持

`CODEX_THREAD_ID`を持つ起動では、`bin/update-dotfiles`とWindows版ランチャーが、uvの外側のnative Pythonで`libexec/update_dotfiles_job.py`へ処理を渡す。
通常端末の更新は既存のTTYとSSH入力を保持する。
ブートストラップがuvと管理Pythonを準備した後、独立workerが更新本体と成功後の必要なpruneを所有する。
起動元のdaemon再起動でcontrollerが終了しても、workerは処理と終了結果の保存を継続する。
process-loopでは稼働中ランタイムの自己更新を避け、uv自己更新と管理Pythonの更新を省く。
成功後の整理には元の引数を渡し、実効minimalまたは明示`--prune`の場合だけuvの外側で実行する。
POSIXは新しいsession、Windowsは独立したprocess groupとjobからの分離を使い、分離失敗を成功扱いにしない。

jobはUUIDで識別し、既存の状態領域の`update-jobs/<UUID>/`へ入力引数・開始終了・終了コードと分離した標準出力・標準エラーを保存する。
環境変数全量は保存しない。
公開`update-dotfiles job`は同じ`CODEX_THREAD_ID`の最新job、`update-dotfiles job <UUID>`は明示jobの保存結果を取得する。
再取得では更新を再実行せず、未完了を完了と区別する。
workerは有限の実行上限と子孫の回収を持ち、通常のCtrl+Cは所有workerへの停止要求と終了回収へ接続する。
各コマンドの標準出力・標準エラーを2本のpipeから継続転送し、直接の子の終了と両pipeのEOFを確認してから結果を公開する。
出力待ちにも同じ上限と停止要求を適用し、読取失敗はjobの失敗へ伝播する。
Linuxはsubreaper、Windowsは停止状態で開始した子を再開前に登録するJob Objectにより、短命な親の終了後も停止対象を所有する。
Linuxの停止ではworkerがまだ回収していない直系の子だけを操作し、親の終了で引き取った孫も列挙し直して回収する。
親の回収後は、数値PGIDが別のプロセス群へ再利用され得るためである。
両pipeを閉じたサービスの存続は完了を妨げず、正常時のWindows Job解放でもそのサービスを停止しない。
完了済みjobは7日で回収し、未完了jobと出力は保持する。

uv内で更新jobを待つ構成は、起動元の停止に更新処理が巻き込まれるため採用しない。
daemon再起動後の会話復旧は既存の起動管理へ接続し、更新jobに別の常駐serverや会話復旧機構を追加しない。

### 更新段の分類と失敗の扱い

段は`libexec/update_dotfiles_stages.py`の`STAGES`で宣言し、中断集合もこの表から導出する。
新しい処理と未知の例外は、失敗を記録して後続へ進む。
続行した失敗はbootstrapも含めて最後に一覧を表示し、本体の成功時も終了コード1へ集約する。
本体が失敗した場合は、本体の終了値を保つ。
中断するのは、更新本体を起動できないか、不確定なソースや配備状態を後続へ渡す段だけである。

| 段 | 失敗時の扱いと回復 | 待機上限 |
| --- | --- | --- |
| git timeout設定の検証 | 不正値は操作を案内して中断 | 同期検証 |
| 更新ロックの取得 | 他の更新の終了を待ち、取得できなければ中断 | 600秒 |
| dotfiles自身のGit更新 | 標準ロックの待機・回収、取得経路と競合の回復の後も失敗すれば中断 | Gitの設定値（既定600秒） |
| chezmoi init | 1回再試行しても失敗すれば中断 | 各300秒 |
| chezmoi apply | 子孫を回収し、通常の失敗は1回再試行してから中断 | 各1800秒 |
| uvの導入、3.15が無い場合の管理Pythonの導入 | 導入を1回再試行し、起動条件を満たせなければ中断 | 各300秒、900秒 |
| agent-toolkitの共有依存環境の準備 | 記録して続行。post-applyと次回の更新が再試行 | 900秒 |
| プラグインのGit更新 | 記録して残りのプラグインと配備を続行 | Gitの設定値 |
| chezmoi status、diff | 表示・記録の失敗として続行 | 各300秒 |
| uvの自己更新、管理Pythonの更新 | 既存版で続行し、次回起動時に再試行 | 各300秒、900秒 |
| Codex管理daemonの停止・再起動 | 記録して続行し、復帰操作を案内 | 停止360秒、起動120秒 |
| 永続ログの開始 | 標準出力・標準エラーだけで続行 | 同期ファイル操作 |
| 更新失敗の起票 | キューを使えない場合と起票の失敗は続行 | 子コマンド120秒 |

所有した子孫を回収できない場合は、残った操作との競合を避けるため中断する。
Gitの`UPDATE_DOTFILES_GIT_TIMEOUT_SEC=0`による上限解除は、明示的なユーザー設定として従来の契約を保つ。
非対話のGitHub SSH取得では、変更前の読取を元の予算内で確認する。
通信が停滞した場合だけ、同じリポジトリのHTTPS URLを今回の取得へ適用し、永続remoteは変更しない。
認証拒否、任意のSSHホスト、対話端末の認証入力はこの切替の対象外とする。
Git更新を省略した適用や、rebase途中のpullの再送では、不確定なソースの配備を防ぐ契約を守れないためである。
chezmoi applyの1800秒は、Linuxの永続ログ179回（2026年10月27日〜11月12日）の最大147.7秒に余裕を持たせた値である。
Windowsでの所要分布は未測定である。

uvと管理Pythonは、その実体で実行器を起動する前の`update_bootstrap.sh`・`.ps1`で更新する。
jobのworkerから自分自身のPythonを入れ替えない。
ブートストラップの段名と分類が宣言表と一致することを試験する。
Linuxはcoreutilsの`timeout --kill-after`で段と子孫の待機を制限し、timeoutが無ければ上限なしで更新を始めない。
bashだけでは孫まで安全に回収できないため、この標準コマンドを追加の前提にした。
Windowsは使用中の実体を共通補助`locking_process.ps1`で分類する。
再起動できるメディアリモコンだけなら停止・更新・復帰し、それ以外のプロセスがあれば何も停止せず見送る。
停止は列挙した生成日時と現在の個体を照合し、保持したプロセスハンドルへ行う。
親の停止で子が先に消えることは正常な停止として扱い、PIDを再利用した別個体は停止しない。
見送りの回数は状態領域に保持し、更新成功で消す。

メディアリモコンの再起動は`setup_media_remote`が所有する。
Windows OpenSSHの配下では`DETACHED_PROCESS`だけの子も接続終了時に消えるため、既存VBSを同じユーザーの対話トークンで一時タスクから起動する。
タスクの完了と、同じユーザーのExplorerが属する非0セッションでの常駐を確認し、タスク登録を回収する。
CIM経由の独立起動だけではSession0になり、稼働PIDとHTTP応答が得られても対話デスクトップへの操作を保証しない。
ログオンしていない場合や対話セッションでの起動を確認できない場合は、復帰の成功として扱わない。

Windowsランチャーは、旧batchが更新中に読み直す`:run`の括弧blockと開始byte位置を保つ。
そのblockより後ろの起動用labelを変更して、ブートストラップへ処理を移した。
終了値の取得、引数の展開とpruneは、旧batchの変数からも実行できる解析済みblockに残す。

失敗の反復は`update_dotfiles_report.py`が段名と正規化した診断のキーで数える。
同じ失敗が3回続き、同じキーの未完了AWIが無い場合だけ、公開CLIの`atk wi add`で起票する。
成功した段の連続回数は消し、キューの無いホストと起票の障害では更新を止めない。
