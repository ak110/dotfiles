# 運用機能: tmuxと通知ベル

## SSH対話ログイン時のtmux自動アタッチ

ホスト単位でSSH対話ログイン時の`tmux`自動アタッチを有効化できる。
`~/.config/dotfiles/tmux-auto-attach`が存在するホストだけで有効になり、存在しないホストでは無効となる。

- 有効化: `autotmux on`／無効化: `autotmux off`／状態確認: `autotmux status`（有効時0・無効時1で終了）

自動アタッチは次の全条件を満たした場合のみ実行される。

- 対話シェル
- SSH経由（`$SSH_CONNECTION`または`$SSH_TTY`が設定されている）
- tmux外（`$TMUX`空）
- 標準入力がTTY
- IDE Remote統合ターミナル外（`$VSCODE_INJECTION`空・`$TERM_PROGRAM`非`vscode`・`$TERMINAL_EMULATOR`空）
- `tmux`コマンド存在
- フラグファイル存在

tmuxセッション名は`main`に固定し、デタッチ時にSSH接続も終了する。
フラグファイルはchezmoi管理対象外でホスト固有運用とする。

## Claude Code入力待ちの通知ベル

Linuxでは、Claude Codeの入力待ち時に端末ベルを送出するフックを
`share/claude_settings_json_managed.posix.json`で配布する。
tmux内では`monitor-bell`（tmuxの初期値は有効）がwindowへベルフラグを設定し、
catppuccinの`@catppuccin_window_flags "icon"`設定によりwindow名へベルアイコンが表示される。

- フックは制御端末のない独立セッションで実行され`/dev/tty`を開けないため、JSON出力の
  `terminalSequence`フィールドでBELを返し、Claude Code自身の端末への書き込みで送出させる
  （公式仕様でtmux内動作が明記されている。対話セッションで画面表示中のみ送出される）
- 質問（AskUserQuestion）は`PreToolUse`のツール名matcherで表示と同時に確定的に鳴らす。
  許可待ち等は`Notification`の入力待ち系4種別（`permission_prompt`・`elicitation_dialog`・
  `elicitation_url_dialog`・`agent_needs_input`）で鳴らす（許可待ちは約6秒後に発火する）。
  AskUserQuestionがNotificationを発生させるかは公式資料に記載が無いため、Notificationを前提としない
- アイドル（`idle_prompt`）はベルの対象に含めない。
  応答終了の約60秒後に発火するため、
  背景のサブエージェント・コマンドの完了を待ってターンを終えた場合も入力待ちと同じ扱いで発火し、
  ユーザーの入力を要さない待機でベルが鳴るためである
- 応答終了そのものは`Stop`のフック（`pytools/claude_hook/stop_bell.py`）で鳴らす。
  常駐ループから起動した自律セッションと、背景のサブエージェント・コマンドが未完了の場合は鳴らさない。
  背景稼働の判定は他のStop系フックと同じ`agent-toolkit/agent_toolkit/_hooks/background_tasks.py`の判定を用いる。
  他のStop系フックがターン継続をblockした場合は、ターンが終了する前にベルが鳴る
- Windowsはtmux運用外のため、ベルの各設定は`share/claude_settings_json_managed.win32.json`へ追加しない
- catppuccinが提供する`icon`の書式はcurrent・lastなど全フラグをアイコン化するため、
  `@catppuccin_window_flags_icon_format`をベル分岐だけへ上書きし、表示対象をベルアイコンに限定する
- tmux本体はアタッチ済みセッションの現在のwindowへベルフラグを設定しないため、
  アクティブなwindow自身ではベルアイコンが増えない（仕様どおりの挙動であり是正対象ではない）

## tmuxウィンドウのコマンド実行中表示

tmux内の対話シェルでコマンドを実行している間は、ステータスライン上のウィンドウ名を`@thm_peach`の前景色と太字で表示する。
プロンプトへ復帰すると通常の表示へ戻る。
ウィンドウ名自体は`automatic-rename`に任せる。

次の条件をすべて満たす対話シェルで有効になる。

- `$TMUX`が設定されている
- `$TMUX_PANE`が設定されている
- `tmux`コマンドが存在する

`.bashrc`のDEBUGトラップがコマンド開始時に呼び出し元ペインのユーザーオプション`@cmd_running`へ`1`を設定する。
`PROMPT_COMMAND`の末尾要素がプロンプト復帰時にこのオプションを解除する。
`.tmux.conf`のcatppuccinウィンドウ書式が`#{P:}`で同じウィンドウ内の全ペインを集約し、いずれかのペインが実行中の場合にウィンドウ名を強調する。

実行状態はペイン単位で保持する。
ウィンドウ単位で保持すると、先に終了したペインの解除が実行を続けている別のペインの表示まで削除するためである。
ペインが破棄されると、そのペインの状態も失効する。

`mise activate bash`は`PROMPT_COMMAND`の先頭へ自身のフックを挿入するため、解除要素は末尾に置く。
末尾以外へ置くと、待機中も実行中として表示され続ける。

対象はbash経由で起動する全コマンドとする。
`$TMUX`または`$TMUX_PANE`が設定されていないシェルと、`tmux`コマンドが存在しない環境では機能を追加しない。
Windowsはtmux運用外のため対象外とする。
