# 起動失敗の観測と無人起動の設計記録

[設計記録の索引](design.md)から参照する、起動失敗の観測と無人起動の記録である。

## 委譲先の起動失敗を待機側が観測できる状態

目的は委譲先の起動が失敗したときに、委譲元が失敗を短時間で受け取り、待機へ進んだ場合も待ち続けずに終わることである。
構造の理由は、待機と再試行の上限を、待機側が観測できる状態と呼び出し側が課す打ち切り閾値の双方から導出する点にある。
初期化の上限と試行回数の積は、起動直後の可用性失敗を待つ上限と直列に続く。
この和がホストのMCPツール呼び出しをバックグラウンドタスクへ移す閾値を上回ると、委譲元は起動の失敗を受け取る前に待機へ進む。
待機側では待機対象の集合が待機中にも増えるため集合が空であることを即時の終了条件にできない。
このため対象が0件である状態の継続に独立した上限を置き、対象0件の継続上限に達した実行は理由を標準エラーへ書いて非0で終える。
対象0件の継続上限は、初期化の失敗が確定し得る最大の経過を上回る値とする。
知識境界として、上限値と導出関係は`agent-toolkit/agent_toolkit/_agents_server/state.py`の定数定義が持ち、待機側の分岐は`agent-toolkit/agent_toolkit/_agents_server/agents_wait.py`が持つ。
委譲元が対象0件の継続上限による終了をどう扱うかは`agent-toolkit/skills/delegation/references/waiting-and-monitoring.md`と`atk agents wait`の公開説明が持つ。
対象が空であることを即時の終了条件にする案は、待機開始後に登録される委譲先を取りこぼすため採用しない。
待機側が`agents_server`の起動処理の進行状況を直接照会する案は、待機CLIと`agents_server`のMCPサーバープロセスの間へ新しい照会の仕組みを要し、既存の状態投影と待機対象登録簿に第3の観測面が加わるため採用しない。

## 委譲先の作業ディレクトリでのプラグイン起動の事前確認

目的は委譲先の作業ディレクトリでagent-toolkitのMCPサーバーとhookを起動できない状態を、委譲先を起動する前に委譲元へ返すことである。
委譲先のClaude Code・Codexは`agents_server`のプロセス環境を継承し、プラグインの起動コマンド（`uv`・`uvx`）を同じPATHと委譲先の作業ディレクトリから解決する。
PATHの先頭にmiseのshimがあると、作業ディレクトリの未trustの設定でshimが失敗する。
Claude Codeはstdioサーバーの接続失敗をホスト共通の記録へ残す。
新規起動・再開のプロセスごとに公開再接続APIを1回試し、未回復の失敗だけをstart/showの警告へ残す。
成否は`claude_mcp_recovered`と`claude_mcp_recovery_failed`へ記録する。
`agents_server`は`start()`（全`mode`が経由する）と終了済みsessionの再開の直前に、委譲先の作業ディレクトリで`uv --version`と`uvx --version`を実行し、失敗時は子を起動せずに`cwd`と標準エラーを含む例外を返す。
process-wiはメインの選定開始前に対象リポジトリのmise trustを準備する。
知識境界として、事前確認のコマンド集合は`agent-toolkit/agent_toolkit/_agents_server/launch_requests.py`の`PREFLIGHT_COMMANDS`が持ち、プラグインの起動コマンドを全て含むことを同モジュールのテストで確かめる。
trustの判断は委譲元の手順（`agent-toolkit/skills/process-wi/references/selection-procedure.md`、`agent-toolkit/skills/process-wi/references/run-lanes.md`）が持つ。
MCP設定の`command`を絶対パスにする案は、`uv`の配置がホストとOSで異なり全エンドユーザーで共通の設定に固定できないため採用しない。
委譲先のPATHからshimを外す案は、委譲先の作業ディレクトリのツール解決を失わせるため採用しない。
`agents_server`が`cwd`を自動でtrustする案は、任意の`cwd`を受け取る`agents_server`がtrustの判断を担うことになるため採用しない。

## 無人セッションと人間の操作を前提とする機構

目的は`agents_server`が起動する委譲先を、人間の承認操作を要さずに起動できる状態に保つことである。
構造の理由は、委譲先が対話UIを持たない無人セッションであり、承認要求と権限申告のいずれにも応答する主体を持たない点にある。
人間の操作を要しない条件は`launch_kind`とengineの種別によらず同じであるため、起動条件は起動区分で分けない。
claudeの委譲先では、権限モードをbypass系にすると、Claude Codeのセッション間メッセージの受信方針が、権限モードを申告しない送信側からのメッセージを保留する。
委譲元は起動直後に権限モードを申告せずにメッセージを送るため、委譲先は保留のまま初期化を完了しない。
このためbypass系以外の権限モードで起動する。
codexの委譲先では、hookの定義が変わると承認済みの記録が無効になり、実行前に承認を求める。
このため`thread/start`と`thread/resume`のリクエストの`config`へ`bypass_hook_trust`を真値として渡す。
知識境界として、claudeの権限モードは`agent-toolkit/agent_toolkit/_agents_server/claude_options.py`、codexの設定は`agent-toolkit/agent_toolkit/_agents_server/codex.py`が持つ。
codexで同名のコマンドラインオプションを渡す案と、`-c`による設定上書きで渡す案は、いずれも`codex app-server`が`bypass_hook_trust`を受理しないため採用しない。
claudeで`crossSessionInbound`設定により受信を拒否する案は、追加設定ファイルとユーザー設定のいずれで渡しても初期化タイムアウトを解消しないため採用しない。
