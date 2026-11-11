# dotfilesの作業ツリーの準備

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 作業ツリーの準備

1. 正しい依存環境を渡すため、専用worktreeや回収予定の検証用複製で`mise trust`を実行し、ルートで`make sync`を実行する。
   リポジトリ直下と`agent-toolkit/`の`.venv`を`uv.lock`と`.python-version`へ合わせ、同期成功後に後続へ渡す。
   - 回収予定の検証用複製には`.git`が含まれる。
     そのため、managed-tempの一時ルート直下に`mktemp -d '<一時ルート>/<用途>-XXXXXXXX'`で作業領域を作成する。
     その領域へ`git worktree add --detach`で複製を作成し、`make sync`で環境を整える。
     通常のファイルコピーで作業ツリーを複製すると、内部にある`.claude/worktrees/`や`.venv`まで実容量で複製してしまう。
   - 回収予定の作業領域ではローカル依存関係の同期のみを行い、Git hookやテンプレートには既存の設定を利用する。
     共有CLIのインストール元や共通のGit設定は、作業領域の回収後も残す必要があるためである。
     `mise run bootstrap`、`mise bootstrap`、`make setup`、`uv tool install --editable`、`prek install`、`git config --local commit.template`の実行は、恒久的に使う作業ツリーの初期設定時に限定する。
     観測と再検証の手順は`docs/development/audit-records-project-environment.md`「develop-dotfiles：回収予定の作業場所の環境準備：2026年10月1日」に記載がある。
2. 信頼登録の不足を防ぐため、複製元と異なる絶対パスで`mise.toml`を参照する領域や、`XDG_STATE_HOME`などで状態ディレクトリを切り替える領域では、作成者が検証前に`mise trust`を実行する。
   信頼登録は設定ファイルの絶対パスに紐づき、状態ディレクトリ配下へ保存される。
   - linked worktreeは複製元の信頼設定を共有するため、複製元リポジトリのルートにある`mise.toml`に対して1回実行すれば足りる。
     検証用の複製では複製先の`mise.toml`の絶対パスを指定し、状態ディレクトリを差し替えた環境では自動チェック時と同じ環境変数を渡して実行する。
   - `MISE_TRUSTED_CONFIG_PATHS`を指定すると既存の信頼登録が上書きされ、複製元が未信頼の状態に戻ってしまうため使用を避ける。
3. 対象版を検証するため、専用worktreeの変更を`atk`で動かす場合（`atk serve`を含む）は、起動するファイルごとに選ばれる版を確認する。
   - PATH上の`atk`が複製元の`bin/atk`を指している場合、カレントディレクトリ（cwd）が同じリポジトリのworktree配下であれば、そのworktreeの`agent-toolkit/scripts/atk`へ処理を委譲する。
     Git管理外や別リポジトリの場合は複製元のバージョンを実行する（`uv run atk`でも同様に判定される）。
   - 配布物に含まれる`scripts/atk`は、その配布物の固定ルートで起動する。
   - 検証や調査を委譲する際は、委譲先へ`<対象worktreeの絶対パス>/agent-toolkit/scripts/atk`を渡し、入れ子で呼ぶコマンドにも同じ絶対パスを指定する。
   - `scripts/launch.py`は、依存に作用するpyproject設定・`uv.lock`・選択したPythonの実行ファイルパス・OS・アーキテクチャから共有環境を選定し、書き込み可能なデータ領域へ配置する。
     配布ルートやプラグイン版だけの違いでは環境を分けない。
   - 使用するCLIの`info`サブコマンドを実行し、動作するルートが検証対象と一致していることを確認する。
4. 利用を終えた領域を残さないため、作業完了と起動プロセスの終了後に`git worktree remove`を実行し、`rm -r -- <作成した絶対パス>`で回収する。
