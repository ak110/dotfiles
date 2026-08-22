# 運用機能の詳細

本リポジトリが配布・設定する運用機能のホスト固有事項と詳細仕様を扱う。
編集方針・ディレクトリ構造は`AGENTS.md`、リポジトリ構成は[architecture.md](architecture.md)を参照する。

## 生成物の一括同期（`sync_generated_files.py`）

生成物の一括同期は`uv run python scripts/sync_generated_files.py`で起動する。

- `uv run scripts/sync_generated_files.py`のようにパスを直接渡すと、
  当該スクリプトのPEP 723ヘッダーが検出され依存なしの隔離環境で実行される
- 同スクリプトは`sys.executable`で生成器を子プロセス起動するため、
  隔離環境では子が要求するプロジェクト依存（`pytilpack`等）を解決できず全件失敗する
- `python`を明示するとスクリプトモードにならずプロジェクト環境で実行される
- 作業ディレクトリを対象の作業用複製（git worktree等）へ変更できない場合は、
  `<複製の絶対パス>/.venv/bin/python <複製の絶対パス>/scripts/sync_generated_files.py`の形で起動する。
  移動コマンドと実行コマンドを同一行に並べる形は、作業ディレクトリの解決が曖昧になるため使わない

## SSH対話ログイン時のtmux自動アタッチ

ホスト単位でSSH対話ログイン時の`tmux`自動アタッチを有効化できる。
既定は無効で、`~/.config/dotfiles/tmux-auto-attach`の有無で切り替える。

- 有効化: `autotmux on`／無効化: `autotmux off`／状態確認: `autotmux status`（有効時0・無効時1で終了）

自動アタッチは次の全条件を満たした場合のみ実行される。

- 対話シェル
- SSH経由（`$SSH_CONNECTION`または`$SSH_TTY`が設定されている）
- tmux外（`$TMUX`空）
- 標準入力がTTY
- IDE Remote統合ターミナル外（`$VSCODE_INJECTION`空・`$TERM_PROGRAM`非`vscode`・`$TERMINAL_EMULATOR`空）
- `tmux`コマンド存在
- フラグファイル存在

tmuxセッション名は`main`に固定し、デタッチ時にSSH接続も終了する。フラグファイルはchezmoi管理対象外でホスト固有運用とする。

## 対話シェル起動時のTBD未回答表示

対話シェル起動時に`atk mq list --type=tbd --answered=no --skip-pull`を自動実行し、未回答TBDを1件1行で画面へ通知する。
`--skip-pull`でログイン時のリポジトリアクセスを避け、0件時は出力なしで終了する。
実行条件は、対話シェルかつ`atk`コマンド存在（`command -v atk`）とする。

## Claude Code入力待ちの通知ベル

Linuxでは、Claude Codeの入力待ち時に端末ベルを送出するフックを
`share/claude_settings_json_managed.posix.json`で配布する。
tmux内では`monitor-bell`（既定有効）がwindowへベルフラグを設定し、
catppuccinの`@catppuccin_window_flags "icon"`設定によりwindow名へベルアイコンが表示される。

- フックは制御端末のない独立セッションで実行され`/dev/tty`を開けないため、JSON出力の
  `terminalSequence`フィールドでBELを返し、Claude Code自身の端末書き込み経路で送出させる
  （公式仕様でtmux内動作が明記されている。対話セッションで画面表示中のみ送出される）
- 質問（AskUserQuestion）は`PreToolUse`のツール名matcherで表示と同時に確定的に鳴らす。
  許可待ち等は`Notification`の入力待ち系4種別（`permission_prompt`・`elicitation_dialog`・
  `elicitation_url_dialog`・`agent_needs_input`）で鳴らす（許可待ちは約6秒後に発火する）。
  AskUserQuestionがNotificationを発生させるかは公式資料に記載が無いため、Notificationに依存させない
- アイドル（`idle_prompt`）はベルの対象に含めない。応答終了の約60秒後に発火するため、
  背景のサブエージェント・コマンドの完了を待ってターンを終えた場合も入力待ちと同じ扱いで発火し、
  利用者の入力を要さない待機でベルが鳴るためである
- 応答終了そのものは`Stop`のフック（`scripts/claude_hook_stop_bell.py`）で鳴らす。
  常駐ループから起動した自律セッションと、背景のサブエージェント・コマンドが未完了の場合は鳴らさない。
  背景稼働の判定は他のStop系フックと同じ`agent-toolkit/scripts/_stop_gate.py`の判定を用いる。
  他のStop系フックがターン継続をblockした場合は、当該ターンの終了前にベルが鳴る
- Windowsはtmux運用外のため、ベルの各経路は`share/claude_settings_json_managed.win32.json`へ追加しない
- `icon`の既定書式はcurrent・lastなど全フラグをアイコン化するため、
  `@catppuccin_window_flags_icon_format`をベル分岐だけへ上書きし、表示対象をベルアイコンに限定する
- tmux本体はアタッチ済みセッションの現在のwindowへベルフラグを設定しないため、
  アクティブなwindow自身ではベルアイコンが増えない（仕様どおりの挙動であり是正対象ではない）

## 質問自動継続タイムアウトの配布

Claude Codeの`askUserQuestionTimeout`は`share/claude_settings_json_managed.json`で`5m`を配布する。
対象は`AskUserQuestion`の選択質問だけであり、権限確認や計画承認を自動継続させる設定ではない。
計時はアイドル時間を基準とし、キー入力でリセットされる。
フォーカスを報告する端末では、ウィンドウへの切り替えでもリセットされる。

`atk mq process-loop`のClaude起動では、利用者設定に依存せず`--settings`で同じ値を明示する。
CLI設定はユーザー設定より優先されるため、配布設定を持たないプラグイン単体利用でも起動時の値を揃えられる。
Claude起動分岐では`CLAUDE_CODE_RETRY_WATCHDOG=1`だけを子プロセス環境へ設定する。`API_TIMEOUT_MS`、
`CLAUDE_STREAM_IDLE_TIMEOUT_MS`及び`CLAUDE_CODE_MAX_RETRIES`はprocess-loopの既定値として設定しない。
該当する障害を実測した環境でだけ、原因に対応する変数を個別に設定する。Codex起動と`update-dotfiles`実行の環境へはClaude専用の値を渡さない。

## mise latestの非ログイン再評価

dotfilesリポジトリを対象とする`atk mq process-loop`は、miseの`latest`指定ツールを非ログイン経路で再評価する。
手動起動時に`mise install --quiet`を一度実行し、その成否後から24時間ごとに待機ループの復帰時に再実行する。

`update-dotfiles`が成功した場合は、その完了時から24時間を数え直す。
更新成功直後の正常再起動では、1回限りの内部引数を次のプロセスへ渡して起動時の重複実行を省く。
内部引数は次の再起動引数から除去するため、ランチャー間の受け渡し形式と永続ファイルを追加しない。

`--no-update`指定時、dotfiles以外を対象とする場合、又は`~/dotfiles`を解決できない場合は再評価しない。
`mise`の終了コードが0以外の場合と600秒でタイムアウトした場合は、結果を標準エラー出力へ警告して常駐処理を続行する。
ログイン時のシェル初期化処理は`mise install`を実行しない。

`chezmoi apply`の後処理も`mise install`を実行する。
miseは実行位置から設定ファイルを探索するため、後処理は`CHEZMOI_WORKING_TREE`に`mise.toml`がある場合だけ
そこを実行位置として呼び出す。実行位置を指定しないとglobal設定だけが対象となり、
working treeにだけ定義したツールが更新を繰り返しても未導入のまま残る。

## Codex診断ログの通常ストレージ復元

Linuxのpost-apply処理は、旧機構が`/dev/shm/codex-<UID>-<ファイル名>`へ配置した診断ログDBを`~/.codex/`へ復元する。
Codexが停止中であり、ホームディレクトリ側の3ファイルが旧機構の正確なリンク又は欠落状態である場合だけ復元する。
復元した実行では共有メモリー側を保持し、後続のpost-apply実行で3ファイルの内容一致を確認してから回収する。

ホームディレクトリ側と共有メモリー側のDB、WAL、SHMが1件でも異なる場合は、自動的に正本を選択しない。
共有メモリー側の集合を`~/.codex/logs_2-restore-conflict-<集合SHA-256>/`へ保存し、復元未完了を警告する。
競合スナップショットは所有者だけが参照できる手動復旧用データであり、post-apply処理は削除しない。
利用者が次の手順を完了するまで保持する。

1. Codexを停止したまま、`~/.codex/`、`/dev/shm/codex-<UID>-*`、競合スナップショットの3集合を照合する
2. SQLiteのDB、WAL、SHMを一組として復旧し、通常ストレージ側の内容を検証する
3. 復旧結果を確認した後に限り、共有メモリー側の旧`target`と競合スナップショットを手動で回収する

## 特定ホストでの常駐サービス自動起動

`euryale`でのみ、`chezmoi apply`後処理が2つのsystemd user serviceを配置して有効化する。
対象は`atk-serve.service`（フィードバック管理Web UI）と`claude-plans-viewer.service`（計画ビューアー）である。

- 待受はいずれもローカルのみで、Web UIはポート28766、計画ビューアーはポート28765を使う
  - ホスト上のブラウザーかSSHポート転送経由に加え、Apacheリバースプロキシ
    （`/etc/apache2/sites-enabled/mysettings-443.conf`、本リポジトリの管理対象外）を使用する。
    Basic Auth付きで`https://tqzh.tk/atk/`・`https://tqzh.tk/cpv/`へ公開する
  - サブパス公開時は`X-Forwarded-Prefix`ヘッダー付与かつプレフィクスを保持したまま
    転送する構成を前提とする。Web UI側は`pytilpack.quart.ProxyFix`でこれを解釈する
  - ホスト固有の待受設定はunitへ書かず`~/.config/agent-toolkit/serve.toml`・
    `~/.config/pytools/claude-plans-viewer.toml`で与える
- Web UIはサービス専用ランチャー`~/.local/bin/atk-serve`を経由して起動する
  - agent-toolkitプラグインはバージョン付きディレクトリへ展開されるためunitへ絶対パスを焼き込めない
  - ランチャーが最新バージョンの`scripts/atk.py`を実行時に解決する
  - `uv`はサービス実行環境のPATHに無いため、導入時に解決した絶対パスをランチャーへ埋め込む
    - 解決順序は`~/.local/bin/uv`（公式インストーラーの導入先）、次にPATH探索とし、
      いずれも得られない場合は設定を見送る
    - miseのshimはサービス実行環境でバージョン未解決となり起動しないため優先しない
  - `~/.local/bin/atk`は`install-claude.sh`が生成する別系統のラッパーで、本経路とは無関係
- 計画ビューアーは`uv tool install`が生成する`~/.local/bin/claude-plans-viewer`を直接起動する
  - shebangが絶対パスのためPATHに依存しない
- 導入処理はrestart後に常駐を確認し、起動しない場合は失敗として`update-dotfiles`の出力へ表示する
- lingerが無効な場合はログアウトで停止する
  - 常駐させるには`sudo loginctl enable-linger <user>`を手動実行する

## Windowsの電源設定の最適化（dotfiles-setup）

`dotfiles-setup`コマンドはWindows専用で、高速スタートアップとUSB selective suspendをまとめて無効化する。

- 高速スタートアップ無効化: `HiberbootEnabled=0`レジストリ書き込みと`powercfg /hibernate off`を実行する
- USB selective suspend無効化: 電源プラン層のAC・DC両系統と、per-device層（`SelectiveSuspendEnabled`と
  `MSPower_DeviceEnable.Enable`。`HKLM:\SYSTEM\CurrentControlSet\Enum\USB`配下）を全USBデバイスへ適用する
- 管理者権限が必要で、未昇格時は`Start-Process -Verb RunAs`でUAC自昇格再起動する（子プロセスはEnter待ち）
- 適用は冪等性を保ち、現在値が望ましい値の場合は変更せず「変更なし」と表示する

## post-applyテンプレートのキャッシュ

`.chezmoi-source/`配下のpost-applyテンプレートはハッシュキャッシュで再実行を抑制し、外部CLIを呼び出す構成をとる。

- 「入力ハッシュ一致」と「期待シム実在」の両方が満たされた場合のみキャッシュを有効と判定する
- `pyproject.toml`の`[project.scripts]`にpost-apply処理継続に必須のCLIを追加・改名した場合は、
  両テンプレートの変数定義節にある`$expectedShims`・`expected_shims`定数を同一値に更新する
- Windowsで実行ファイル、DLL、仮想環境などを更新する場合は、対象を保持するプロセスの所有者と再起動可否を更新前に分類する
- 更新処理が所有し、元の稼働状態へ復元できるプロセスだけを停止する
- 更新対象の実行ファイル・DLL・仮想環境を、所有しないか復元できないプロセスが保持する場合は、次回への延期、既存版の温存、補助更新の非致命化から処置を選ぶ。
  補助更新の失敗を主目的の更新停止条件にしない

## chezmoiの命名規則（早見表）

`.chezmoi-source/`配下のファイル名は以下の代表規則で`~/`配下にデプロイされる。
詳細は<https://www.chezmoi.io/reference/source-state-attributes/>を参照する。

- `dot_<name>` → `~/.<name>`
- `private_<name>` → パーミッション`600`／ディレクトリは`700`
- `executable_<name>` → 実行権限付きで配置
- `<name>.tmpl` → Goテンプレートとして評価
- `run_onchange_after_<name>.sh.tmpl` → `chezmoi apply`時の変更検知実行
- よく使うコマンド: `chezmoi apply`（反映）・`chezmoi diff`（差分確認）・`chezmoi managed`（配布対象確認）
