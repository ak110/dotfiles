# 障害・欠陥の記録: agents_serverと委譲基盤の不全（2026年8月28日〜2026年9月22日）

[agents_serverと委譲基盤の不全](incidents.md#agents_serverと委譲基盤の不全)の事例を発生時期別に収録する。

- 2026年8月28日: 複数レーンの計画レビュー中に、先に完了した計画担当へ指摘を返す時点で30分の結果保持期限を超え、`send_message`が`session retention expired`を返して同一会話を再開できなかった。
  直接原因: Codexの`thread/resume`とClaude Agent SDKの`resume`は保持中sessionの内部replyだけから利用でき、期限切れ後に呼び出し元の正確な識別子から到達する公開入力がなかった。
  対策: 結果本文の回収後も`engine`・`cwd`・`model`・`effort`を最小状態として保持し、同じ`send_message`が両engineの保存済み会話を暗黙に再開する。
  両engineの期限切れ再開と正確な識別子配送を近接テストで固定する

- 2026年8月30日: `engine=claude`の委譲先への`send_message`が応答を返さず、その後の`wait`が`session retention expired`を返して委譲工程が停止した。
  直接原因: 継続要求の応答futureを解決する主体がsession所有タスクの正常終了時だけであり、保持期限の到達、message streamの例外、タスクの取り消しで所有タスクが終了する場面に未解決futureを解決する処理が無かった。
  対策: 要求の受理と応答の解決を1つの継続要求チャネルへ集約し、所有タスクのいずれの終了条件でも受理済みの要求を解決する。
  所有タスクが終了したsessionへの継続送信は保持期限の経過と同じ再開処理へ回し、保持中の直前結果のうち`wait`または`kill`で回収されていないものを`previous_result`として返す

- 2026年9月1日: plugin cacheの更新後、長寿命の`agents_server`が保持するCodex App Serverが`thread/start`へ同じJSON-RPC errorを返し続けた。
  直接原因: 子App Serverの作業ディレクトリをMCPサーバー自身から継承していた。
  Codexホストではpluginの版数付きcacheディレクトリが作業ディレクトリになる。
  対策: 子App Serverの作業ディレクトリへ実行主体のホームディレクトリを明示指定する。
  親の作業ディレクトリを削除した状態で`thread/start`が成功することを近接テストで固定する

- 2026年9月2日: Codexの利用枠が上限に達した状態で、`agents_server`の`start`が返したsessionが直後に失敗し、委譲工程が3件停止した。
  直接原因: サーバーが可用性の失敗を判定せず、次候補への切替を`exclude_session_id`付きの再起動という呼び出し側の操作だけに委ねていた。
  対策: `start`・`start_explore`が起動直後の終了を上限付きで確認し、engineの可用性で終了した候補を除外して次候補で起動する。
  全候補が起動不能な場合だけ失敗を返す

- 2026年9月6日: 委譲先が`agents_server`で起動した孫sessionの終了を観測しないまま呼び出し元の`wait`が終了時の結果を返し、孫が書き込む作業ツリーへ次の書込主体が接続した。
  直接原因: `SessionState`がターン中に起動されたsessionの識別子を保持せず、別プロセスの終了状態を照会する共有記録も無かった。
  対策: 両backendがMCPツール結果から孫sessionを追跡し、プロセス間のsession登録簿で全ての終了を観測するまで結果を保留する。
  終了後は同じsessionへ一度だけ継続指示を配送し、保持期限まで未観測の識別子は既存の`error`項目へ示す

- 2026年9月15日: `agents_server`の`start`と`start_custom`で起動したClaudeの委譲先が、全ての再試行を含めて初期化タイムアウトで失敗し、専用タスク文書を使う委譲が1件も成立しなかった。
  直接原因: 委譲先を`bypassPermissions`で起動していた。
  Claude Codeのセッション間メッセージの受信方針は、受信側がbypass系であり送信側が権限モードを申告していない場合に、送られたメッセージを保留する。
  対策: 起動区分によらずbypass系以外の権限モードで起動する。
  委譲先CLI自身の診断を起動ごとに状態ディレクトリ配下へ保存し、保持世代を超えた記録を自動削除する。
  初期化タイムアウトの診断へ、受信メッセージを識別できる要約と委譲先プロセスの子プロセス一覧を含める

- 2026年9月15日: Claude CodeのAgentツールで起動した委譲先が`Agent terminated early due to an API error`で早期終了した。
  直接原因: 中継した外部エンドポイントが解釈しないベータ由来の`role`が`system`のcontent配列を、Claude Code CLIが送出して400になり、CLIのベータ除外のフォールバックも発動しなかった。
  対策: 全実験ベータを無効化する`CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS`を配布設定へ加える案は、他の実験ベータもまとめて無効化し、特定の中継環境に固有の事象に対して全実行環境の機能を恒常的に縮小するため採らない。
  防御は400を記録した中継側に委ね、本リポジトリ側は上流のClaude Code CLIが観測されたエラー本文を分類対象へ加えるまで待つ。
  再検証は問題の中継環境の受信エラー記録に`system content must contain at least one block`が現れないことの確認で行う
- 2026年9月15日: プラグインの更新でhookの定義が変わった後、`agents_server`が開始するcodexの委譲先が起動しなかった。
  直接原因: codexは承認済みの記録が無効になったhookの実行前に承認を求めるが、委譲先は対話UIを持たず承認要求へ応答する主体が存在しなかった。
  対策: `thread/start`と`thread/resume`のリクエストの`config`へ`bypass_hook_trust`を真値として渡す。
  同名のコマンドラインオプションと`-c`による設定上書きは`codex app-server`が受理しないため用いない
