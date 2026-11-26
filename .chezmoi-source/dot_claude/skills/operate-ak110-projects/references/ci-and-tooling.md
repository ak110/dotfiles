# gv・lcとCI・ビルドの補足

Windows用プロジェクト（gv・lc）をLinuxから扱う注意と、prek・pyfltr・文書lintの呼び出し、CI workflowを編集する時の確認観点である。
正式な手順と実値は、各リポジトリの`AGENTS.md`、`docs/development/development.md`、`.github/workflows/**`にある。

## gv / lc（Windows用プロジェクト）の特殊事情

- `~/gv`: Debian/Ubuntuで`mise install`の後に`mise run setup-linux`を実行すると、cargo-xwinによるWindowsターゲットのクロスビルド環境がGit管理外の`.mise.local.toml`へ設定される。
  以後はWindowsと同じ手順で`mise run test`・`mise run build`とpre-commitのpyfltr hookを使える。
  Linuxの`mise run test`はclippy・cargo-deny・テストのコンパイルまでを行い、テストの実行はWindows CIで確かめる。
  LinuxビルドはRAR展開を除外する（`--cfg gv_no_rar`）。
- `~/lc`: `mise run setup-linux`で環境を構築すると、`mise run test`（`dotnet-test`を除く全チェック）と、配布用exeを出力する`mise run build`を実行できる。
  `dotnet-test`はWindows CIで確かめる。
- 両リポジトリとも、Linuxからは`LOCALAPPDATA`を与えずにmiseを起動できる。
  `~/gv`の`mise.toml`はこの値を参照せず、`~/lc`の`mise.toml`はWindows用タスクの内側でだけ参照するためである。
- `mise.toml`のタスクを使う。
- cargo-denyは`taiki-e/install-action@v2`と`with: tool: cargo-deny`で導入し、actionをpinactのハッシュピンの対象にする。
  `taiki-e/install-action@cargo-deny`の短縮形を保つ場合だけ、そのactionを`.pinact.yaml`のハッシュ固定の対象から除く（ツール名タグのSHA固定は更新後に参照できなくなりうるため、公式が強く非推奨としている）。

## prek / pyfltr / ビルド関連

- Gitフックはmise管理のprekで導入する。
  `mise.toml`の`[tools]`にprekの版を指定し、`mise install`の後に`mise exec -- prek install -c .pre-commit-config.yaml`を実行する。
  既存の導入タスクも同じ実行ファイルを使う。
  フックに記録されるprekのパスをuvの取得キャッシュに依存させないためである。

- 全プロジェクトでprekが`pyfltr fast`を実行し、Markdownの変更には`markdownlint-fast`と`textlint-fast`が軽量に働く。
- `~/dotfiles`はdev依存へ固定した`uv run --frozen pyfltr fast`を、`~/pyfltr`は自身を`uv run`で呼び出し、その他のプロジェクトは`uvx --exclude-newer-package pyfltr=false pyfltr fast`を呼び出す。
  最後の形は公開直後のpyfltrを公開待機の対象から外して使う作者個人の対処であり、pyfltrの推奨ガイドが示す呼び出し形はそのまま保つ。
  この除外は、グローバルのuv設定（`~/.config/uv/uv.toml`）ではなくコマンドラインで指定する。
- 文書lint（textlint、markdownlint、prettier）はpyfltr経由（miseタスク、prekのhook、CI）で行い、`AGENTS.md`を含む文書をpyfltrの対象集合で検査する。
  `package.json`の`scripts`へ、pyfltrと別に文書lintを動かすスクリプト（`lint`、`lint:fix`など）を置かない。
  起動するコマンドごとに対象集合が分かれ、片方の結果だけでは他方の対象にある警告が漏れるためである。
  新規のNode系プロジェクトも同じとし、pyfltrがlintツールを`node_modules/.bin/`から起動する設定では、`devDependencies`のlintパッケージを残す。

## CI / リリース関連

CI workflowを編集する時は、次を確かめる。
共通方式と異なる案は、個別に採用する前に[同期の手順](project-synchronization.md#同期の手順)で扱う。

- 通常のLinux CIでコードや配布物を検証する対象ジョブはpyfltrの公式イメージを使う `container:` 実行にそろえる。
  dotfilesのホスト上のセットアップやRustの別ジョブは保持する。
  環境そのものを試すジョブ、Windows、定期監査、成果物を公開する配布・リリースは現行の構成を保つ。
  採否の根拠と対象は[追従台帳のLinux CIの共通方式](followup-ledger-settings-testing.md#linux-ciの共通方式をコンテナーにそろえる)にある。
- container化したジョブでは、uv・pnpm・Node.js・miseのセットアップのstepを省く。
- `defaults.run.shell: bash`を指定する。
  GitHub Actionsは、`container:`のジョブで`shell`の指定が無い`run`を`sh`で実行するためである。
- Pythonの版の行列は`env: UV_PYTHON: ${{ matrix.python-version }}`で引き継ぐ。
- Actionsのピン留めの検査は、`pyproject.toml`の`[tool.pyfltr]`の`preset = "latest"`で有効になる`pinact`へ集約する。
  対象は`ci.yaml`の`pyfltr ci`と、push前の`pyfltr run`・`pyfltr fast`（prekのpre-commitを含む）とし、独立したstepは対象外とする。
- `release.yaml`の`GH_TOKEN`は`${{ github.token }}`を使う。
- `release.yaml`のCI待機は、対象のcommitを指定してCIワークフロー（`ci.yaml`）の実行を直接照会し、その結論で判定する。
  `check-suites` APIの先頭のsuiteで判定すると、リリースワークフロー自身のsuiteを取得し、CIが成功していても待機がタイムアウトする。
  照会のコマンドは各リポジトリの`.github/workflows/`にあり、本文には所在だけを書く。
- `container:`で実行するジョブのstepへ新しいコマンドの呼び出しを足す時は、先行するstepで導入されるか、ジョブが宣言する`image`にそのコマンドがあるかを確かめる。
  どちらでも確かめられないコマンドは、呼び出す前に同じジョブで導入する。
  `ENTRYPOINT`がシェルでないimageでは、`docker run --rm --entrypoint sh <image> -c 'command -v <command>'`で確かめる。

## PRの依存差分チェック

dotfilesでは、master宛ての全PRで`GitHub Dependency Review`を実行する。
パスで限定せず、ベースブランチをmasterへ変更した場合にも実行する。
依存変更のないPRも成功の結果を返す。
ライセンスチェックとOpenSSF Scorecardの表示は無効にし、全スコープ（runtime・development・unknown）の既知の脆弱性を検出する。
比較APIのエラーは成功扱いにしない。
比較が成功しても `x-github-dependency-graph-snapshot-warnings` の値に警告があれば同じジョブを失敗させる。
固定したActionが比較に使った応答の警告を要約から確認し、要約を取得できない場合も失敗させる。
ヘッダーが無いか値が空なら、警告のチェックは成功とする。

依存グラフが認識する `uv.lock`、`rust/claude-statusline/Cargo.lock`、workflowのaction、miseのロック配下の `package.json` は各定義の変更が比較結果に現れることを検証用PRで確かめる。
SBOMに載るだけではPRで比較される証拠にしない。
比較に現れない定義はチェック対象から外し、理由と別の監査の有無をここに記録する。

- 定期監査の `audit.yaml` は残す。
  PRのチェックとは別に、後から判明した `uv.lock` と `rust/claude-statusline/Cargo.lock` の脆弱性も検出する。
- `agent-toolkit/scripts/launch.py.lock` は依存グラフの直接の対象外。
  配布先の実行依存は `agent_toolkit_dependencies_invariant_test.py::test_locked_versions_match_root_lock` がルートの `uv.lock` との版の一致をチェックし、ルートのチェックと監査で間接的に覆う。
- `mise.lock` と `.chezmoi-source/dot_config/mise/mise.lock` が固定するツールの版は、PRのチェックと既存の定期監査のどちらにも含まれない。

通常のPRで結果が返ることを確かめてから、dotfilesの `master-release-pr` ルールセットに `Dependency Review` を必須チェックとして加える。
他のリポジトリへは検知のみを導入し、ルールセットを新設しない。
追従先でも、対象にする依存定義の変更が比較結果に現れることと、比較データを取得できない場合に成功しないことを確かめる。

| リポジトリ | 対象または除外理由 |
| --- | --- |
| pyfltr | Python依存を検知する。 |
| glatasks | `pnpm-lock.yaml` のnpm依存を検知する。 |
| gv | Cargoとnpmの依存を検知する。 |
| lc | npm依存を検知する。中央管理の `Directory.Packages.props` は一部しか認識されないため、NuGetは既存の定期監査に任せる。 |
| pytilpack | 除外。ライブラリのlockfileは開発専用で、意図的に依存の警告を無効にしている。 |
| smpr | 除外。非公開リポジトリの Dependency Reviewに必要な GitHub Advanced Security を個人アカウントでは使えない。 |
