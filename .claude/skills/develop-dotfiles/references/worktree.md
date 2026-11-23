# dotfilesの作業ツリーの準備

## 作業ツリーの準備

1. 専用worktreeや回収予定の検証用複製では、`mise trust`の後にルートで`make sync`を実行し、リポジトリ直下の`.venv`を`uv.lock`と`.python-version`へ合わせてから後続へ渡す（`agent-toolkit/`には仮想環境を作らない）。
   - 検証に使う作業ツリーの専用環境を用意する。
     `make sync`は同期前に環境の実体を確かめ、リンクされた既定の環境や別の作業ツリーに属する環境を拒否する。
   - `make format`・`make test`・`make test-browser`・`make test-agents-live`は同期後の検証とその子の`uv run`に`UV_NO_SYNC=true`を渡す。
     Makeを経由しないuvや明示の`uv sync`を起動する時も、作業ツリーの専用環境を確認する。
   - 回収予定の検証用複製は、managed-tempの一時ルート直下に`mktemp -d '<一時ルート>/<用途>-XXXXXXXX'`で作った領域へ`git worktree add --detach`で作る。
     通常のファイルコピーで複製すると、内部の`.claude/worktrees/`や`.venv`まで実容量で複製してしまう。
   - 一時複製でWindowsの更新を最後まで試す場合は、[Windowsの更新を最後まで試す](verification.md#windowsの更新を最後まで試す)を使う。
   - 回収予定の領域ではローカルの依存の同期だけを行い、Git hookとテンプレートは既存の設定を使う。
     共有CLIの導入元と共通のGit設定は、領域の回収後も残す必要がある。
     そのため`mise run bootstrap`、`mise bootstrap`、`make setup`、`uv tool install --editable`、`prek install`、`git config --local commit.template`は恒久的に使う作業ツリーの初期設定に限る。
     観測と再検証の手順は`docs/development/audit-records-project-environment.md`「develop-dotfiles：回収予定の作業場所の環境準備：2026年10月1日」に記載がある。
2. 複製元と異なる絶対パスで`mise.toml`を参照する領域と、`XDG_STATE_HOME`などで状態ディレクトリを切り替える領域では、作成者が検証の前に`mise trust`を実行する。
   信頼登録は設定ファイルの絶対パスに紐づき、状態ディレクトリ配下へ保存される。
   - linked worktreeは複製元の信頼設定を共有するため複製元のルートの`mise.toml`への1回の実行で十分である。
   - 検証用の複製では複製先の`mise.toml`の絶対パスを指定し、状態ディレクトリを差し替えた環境では自動チェックの時と同じ環境変数を渡す。
   - `MISE_TRUSTED_CONFIG_PATHS`は既存の信頼登録を上書きして複製元を未信頼へ戻すため、指定を避ける。
3. 専用worktreeの変更を`atk`で動かす場合（`atk serve`を含む）は、起動するファイルごとに選ばれる版を確かめる。
   - PATH上の`atk`が複製元の`bin/atk`を指す時、cwdが同じリポジトリのworktree配下ならそのworktreeの`agent-toolkit/scripts/atk`へ処理を委ね、Git管理外や別リポジトリでは複製元の版を実行する（`uv run atk`も同じ判定）。
   - 配布物の`scripts/atk`は、その配布物の固定のルートで起動する。
   - 検証や調査を委譲する時は、委譲先と入れ子で呼ぶコマンドへ`<対象worktreeの絶対パス>/agent-toolkit/scripts/atk`を渡す。
   - 使うCLIの`info`サブコマンドで、動作するルートが検証の対象と一致することを確かめる。
4. 作業を終え、起動したプロセスが終了したら、`git worktree remove`と`rm -r -- <作成した絶対パス>`で領域を回収する（原則7、`agent-basics:writing-standards/references/quality-principles-ownership.md`）。
