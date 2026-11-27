# dotfilesの作業ツリーの準備

## 作業ツリーの準備

1. 専用worktreeや回収予定の検証用複製では、`mise trust`の後にルートで`make sync`を実行し、リポジトリ直下の`.venv`を`uv.lock`と`.python-version`へ合わせてから後続へ渡す（`agent-toolkit/`には仮想環境を作らない）。
   - `make sync` の同期対象はPythonの仮想環境に限る。
     mise管理のツールやshimは別に準備する。
   - 検証に使う作業ツリーの専用環境を用意する。
     `make sync`は同期前に環境の実体を確かめ、リンクされた既定の環境や別の作業ツリーに属する環境を拒否する。
   - `make sync`がPyPIの名前解決で失敗した時は、作業ツリーの専用環境へ同期する権限と、利用が許可されたローカルキャッシュがあれば、`UV_OFFLINE=1 make sync`を実行する。
     `uv.lock`と`.python-version`に必要な依存とPythonが揃っていれば同期できる。
     同期後の`uv run`にも`UV_OFFLINE=1`を渡す。
     必要なキャッシュが無いか、同期自体が許可されない時は、同期を要する検証を未実施として報告する。
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
   信頼する設定ファイルは絶対パスで指定し、信頼登録は状態ディレクトリ配下へ保存される。
   - linked worktreeは複製元の信頼設定を共有するため複製元のルートの`mise.toml`への1回の実行で十分である。
     `.claude/worktrees/`配下で主checkoutの`mise.toml`が未信頼と出る時、worktree側の`mise.toml`だけを信頼しても直らない。
     許可された専用状態領域を検証に使う場合は、Linuxでは`XDG_STATE_HOME='<専用状態領域の絶対パス>' mise trust '<主checkoutの絶対パス>/mise.toml'`を実行し、検証にも同じ`XDG_STATE_HOME`を渡す。
     状態領域の指定や信頼登録が許可されない時は、miseを要する検証を未実施として報告する。
   - 検証用の複製では複製先の`mise.toml`の絶対パスを指定し、状態ディレクトリを差し替えた環境では自動チェックの時と同じ環境変数を渡す。
   - `MISE_TRUSTED_CONFIG_PATHS`は既存の信頼登録を上書きして複製元を未信頼へ戻すため、指定を避ける。
3. 専用worktreeの変更を`atk`で動かす場合（`atk serve`を含む）は、起動するファイルごとに選ばれる実体を確かめる。
   - PATH上の`atk`が複製元の`bin/atk`を指す時、cwdが同じリポジトリのworktree配下ならそのworktreeの`agent-toolkit/scripts/atk`へ処理を委ね、Git管理外や別リポジトリでは複製元の実装を実行する（`uv run atk`も同じ判定）。
   - 配布物の`scripts/atk`は、その配布物の固定のルートで起動する。
   - 検証や調査を委譲する時は、委譲先と入れ子で呼ぶコマンドへ`<対象worktreeの絶対パス>/agent-toolkit/scripts/atk`を渡す。
   - 使うCLIの`info`サブコマンドで、動作するルートが検証の対象と一致することを確かめる。
   - worktreeのコードから`agents_server`を直接起動する確認では、起動元とその子に専用の状態領域を渡す。
     Linuxは`XDG_STATE_HOME`、Windowsは`LOCALAPPDATA`を`managed-temp`内の専用ディレクトリの絶対パスに設定する。
     通常の委譲の世代登録簿を使わない。
4. 作業を終え、起動したプロセスが終了したら、`git worktree remove`と`rm -r -- <作成した絶対パス>`で領域を回収する（原則7、`agent-basics:writing-standards/references/quality-principles-ownership.md`）。

### PyrightがPython環境を解決できない時

NodeからのPython環境照会に失敗すると、導入済みの依存にもimportの診断が出る。
`--pythonpath`だけではその照会を省けない。
Pyrightの実行が許可され、対象worktreeに同期済みで利用を許可された`.venv`がある時に限り、禁止された照会を再試行せず、次の一時設定で依存先を明示する。

1. `pyproject.toml`の`[tool.pyright]`をJSONへ写し、managed-tempに`.json`ファイルとして保存する。
   元の設定や対象ファイルは編集しない。
2. `extraPaths`と`ignore`はworktree基準の絶対パスにする。
   `include`と`exclude`は一時設定からの相対パスに付け替える。
   globは展開せず保つ。
   元の`include`が省略されている時はworktreeのルートを指定し、`exclude`には元の設定に加えてPyrightが元のルートに適用する既定パターン（`--verbose`の`Auto-excluding`）も含める。
   元の既定のimport探索を保つため、`executionEnvironments`が無ければworktreeの絶対パスを`root`とする要素を指定する。
   `stubPath`の指定がなくworktreeに`typings`がある時は、その絶対パスを`stubPath`に指定する。
   元の設定と一時設定の各パスを同じ基点で比べる。
   対象範囲とプロジェクト内のimport探索先を保てない時は、一時設定では実行しない。
3. `venvPath`をworktreeの絶対パス、`venv`を`.venv`、`pythonVersion`を`.python-version`のメジャー・マイナー版にする。
4. worktreeのルートで`UV_NO_SYNC=true uv run --frozen pyright --project '<一時設定の絶対パス>' --verbose '<検査対象のPythonファイル1件>'`を実行し、環境を確認する。
   Pyrightが起動しなければ未実施とする。
   起動したら、指定したPython版とworktreeの`.venv`の`site-packages`が使われ、環境照会の失敗が出ていないことを詳細出力で確認する。
   環境を解決できなければ、コマンドと診断を記録し、合否を付けず検証未完了とする。
5. 環境を解決できたら、同じworktreeで`UV_NO_SYNC=true uv run --frozen pyfltr run --no-fix --commands=pyright --pyright-args='--project "<一時設定の絶対パス>"'`を実行する。
   元の検証で対象パスを指定していた時は同じパスを渡す。
   pyfltrは`--outputjson`を付けるため、Pyrightの`--verbose`と併用できない。
   `--verbose`は前の環境確認だけに使う。
   正しい環境で診断が出た場合は、importに関するものも不合格として修正する。
