# 運用機能: miseの導入と再評価

## miseの導入と再評価

### process-loopの非ログイン再評価

dotfilesリポジトリを対象とする`atk wi process-loop`はmiseの`latest`指定ツールを非ログインシェルから再評価する。
手動起動時に`mise install --quiet`を一度実行し、その成否後から24時間ごとに待機ループの復帰時に再実行する。

`update-dotfiles`が成功した場合は、その完了時から24時間を数え直す。
更新成功直後の正常再起動では、1回限りの内部引数を次のプロセスへ渡して起動時の重複実行を省く。
内部引数は次の再起動引数から除去するため、ランチャー間の受け渡し形式と永続ファイルを追加しない。

`--no-update`指定時、dotfiles以外を対象とする場合、または`~/dotfiles`を解決できない場合は再評価しない。
`mise`の終了コードが0以外の場合と600秒でタイムアウトした場合は、結果を標準エラー出力へ警告して`atk wi process-loop`を続行する。
ログイン時のシェル初期化処理は`mise install`を実行しない。
対話シェルの初期化が起動する外部コマンドのうち、miseのshimとして解決され得るものは`MISE_AUTO_INSTALL=false`を与えて起動する。
これにより、未導入ツールのshimを実行してもmiseは導入を始めない。
導入は本節の非ログイン再評価と`chezmoi apply`の後処理だけが担う。
配布するglobal設定は`gitlab.glab_cli_tokens`を無効にする。
miseはglab CLIの設定ファイルから取得したトークンを公開APIの呼び出しへ付与するため、期限切れのトークンは401を返す。

### `chezmoi apply`の後処理の`mise install`

`chezmoi apply`の後処理も`mise install`を実行する。
fullでは次の作業ツリーを選択し、minimalではホームからグローバル設定の軽量ツールだけを対象とする。
miseは実行位置から設定ファイルを探索するため、後処理は`CHEZMOI_WORKING_TREE`に`mise.toml`がある場合だけ
そこを実行位置として呼び出す。
実行位置を指定しないとglobal設定だけが対象となり、
working treeにだけ定義したツールが更新を繰り返しても未導入のまま残る。

成功した導入・整理の後は、miseが列挙した設定ファイル・lock・導入状態とshimの内容を記録する。
次回も内容が同じなら`install`・`prune`・`reshim`を省き、実行ファイルの配置先の欠落検査は毎回行う。
内容は実バイトで比較するため、同じサイズ・更新時刻での設定編集も反映する。
導入や整理の失敗は成功記録を更新せず、次回に再試行する。

WindowsでWinGet管理のmiseを使う場合は、実行中の本体版と`winget show --versions`の候補版を比較する。
本体が候補版以上なら`winget upgrade`を省き、同じ本体版に対する確認結果を24時間再利用する。
`remove_all: Access is denied`では、miseを使うターミナルやエディターを終了し、新しいターミナルで`update-dotfiles`を再実行する案内を出す。
展開時の`os error 1314`では、Windowsの設定で「開発者モード」を検索して有効にする操作を案内する。
miseの後処理はプロセスを強制終了せず、OSの権限設定も変更しない。

後処理は`mise install`の後、`mise prune`の前に、設定済みツールの実行ファイルの配置先（引数なしの`mise bin-paths`が返すディレクトリ）の実在を確かめる。
実在しない配置先は、`mise ls --current --installed --json`の`install_path`を親に持つツールと版へ対応付け、同じ実行位置で`mise install --force <ツール>@<版>`により再導入する。
`symlink_bins`など配置先を変えるツールオプションはmiseが導入時にだけ配置を生成し、`mise install`は導入済みの版を再導入せず、`mise install --dry-run-code`と`mise doctor`も欠落を示さないためである。
どの`install_path`の配下にも無い配置先（共有ランタイムへのリンクで導入されるツール）は再導入しない。
再導入にはlockedモードの環境変数を与えない。
mise 2026.9.17では、lockにURLがあってもlockedモードの`mise install --force <ツール>@<版>`は失敗し、版を明示した再導入はlockfileを書き戻さないためである。
検出と再導入の失敗、および再導入後も残る欠落は警告を出力して後処理を続け、残る欠落の警告には手動の復旧コマンドを含める。

### lockedモードの環境変数

process-loopと後処理の`mise install`へは環境変数`MISE_LOCKED=1`と`MISE_LOCKED_SCOPES=project`を与える。
これにより、プロジェクトの`mise.lock`に記録済みの解決結果から導入するlockedモードで動かす。
`MISE_LOCKED`を与えない`mise install`は実行位置のlockfileを書き戻し、書き戻す内容は実行ホストのプラットフォームで変わる。
このため`MISE_LOCKED`を与えない場合は、dotfilesの作業ツリーの`mise.lock`に差分が残る（Windowsで`mise.toml`がLinux x64だけに指定した配布物のエントリが消えた）。
global設定は`npm:`系などlockにURLを持たないツールを含むため、`MISE_LOCKED_SCOPES=project`でlockedの対象から外す。
lockedモードの`install`が失敗しても、lockを書き戻すlockedなしの再実行はしない。
`mise.toml`にあってlockに無いツールによる失敗はlockの更新不足であり、`make update-mise-locks`で解消する。
`update-dotfiles`はgit pull工程の前にルート`mise.lock`の差分を破棄してHEADの内容へ戻し、それ以外の未コミット差分だけを退避して復元する。

`update-dotfiles`の各段は後続の段の前提になる段と、失敗しても後続の段の前提を壊さない段に分ける。
前提になる段は、ロックの取得、git timeout設定の検証、dotfiles自身のGit更新、`chezmoi init`、`chezmoi apply`である。
これらが失敗したときは、ソースの状態が不確定なまま配備しないよう、その時点で止める。
前提を壊さない段は、過去の退避データの整理、`mise.lock`の差分の破棄、プラグインのpull、`chezmoi status`、`chezmoi diff`、
Codex管理daemonの一時停止と再起動、永続ログの開始である。
これらが失敗したときは、失敗を記録して後続の段を続ける。
最後に失敗した段と次の操作の一覧を標準エラーへ表示し、終了コード1で終える。
分類の詳細は`libexec/update_dotfiles.py`のモジュールdocstringにある。
