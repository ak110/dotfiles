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

- Linuxジョブはpyfltrの公式イメージによる`container:`実行とし、container化の対象とキャッシュの方式は各リポジトリの`.github/workflows/**`にそろえる。
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
