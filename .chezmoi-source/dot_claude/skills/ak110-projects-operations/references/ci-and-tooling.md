# gv・lcとCI・ビルドの補足

本書は`ak110-projects-operations`の`SKILL.md`の読込表から読む参照資料であり、Windows用プロジェクト（gv・lc）をLinuxから扱う注意と、prek・pyfltr・文書lintの呼び出し、CI workflowを編集するときの確認観点を持つ。

## gv / lc（Windows用プロジェクト）の特殊事情

- Linuxでの検証はlint系（textlint / markdownlint / prettier）のみ確認可能
- Makefileではなく`mise.toml`のタスクを使用する。prekフレームワークは`uvx prek`で呼び出す
- cargo-denyの導入は`taiki-e/install-action@v2`と`with: tool: cargo-deny`を用い、
  actionをpinactのハッシュピン対象にする。
  `taiki-e/install-action@cargo-deny`のshort-handを維持する場合だけ、そのactionを`.pinact.yaml`の
  ハッシュ固定対象から除外する（ツール名タグのSHA固定は更新後に参照不能となり得るため
  公式に強く非推奨であることによる）

`~/gv`の`mise.toml`が参照する値に`LOCALAPPDATA`は含まれず、`~/lc`の`mise.toml`はWindows用タスクの内側でだけ参照するため、Linuxでの設定読み込みはこの値と無関係に成立する。
両リポジトリでは、Linuxからmiseを起動するための`LOCALAPPDATA`の付与は不要である。

加えて`~/gv`のRustコードは、`windows-future`等のWindows専用クレートが依存ツリーに含まれるため、
Linux環境で`cargo check`・`cargo clippy`・`cargo test`がビルド段階で失敗する。
Linuxから`~/gv`のRustコードを変更する場合は次のいずれかで対処する。

- Windows実機で`cargo`系チェックを実行してからpushする
- `SKIP=pyfltr`でcargo系チェックを含むhookを無効化してコミットし、cargo対象外の変更パスへ`uvx --exclude-newer-package pyfltr=false pyfltr run`を実行する
- 該当コードを`#[cfg(windows)]`ガードで囲み、Linux向けビルド対象外にする

## prek / pyfltr / ビルド関連

- 全プロジェクトでprekフレームワークにより`pyfltr fast`が実行される
  - `markdownlint-fast`／`textlint-fast`によりmd変更時のlintが軽量に実行される
  - `~/dotfiles`はdev依存へ固定した`uv run --frozen pyfltr fast`を、`~/pyfltr`は自身を`uv run`で呼び出す。
    その他のプロジェクトは`uvx --exclude-newer-package pyfltr=false pyfltr fast`を呼び出す。
    公開直後のpyfltrを公開待機の対象から外して使うための作者個人の対処であり、pyfltrの推奨ガイドが示す呼び出し形はそのまま保つ。
    例外はコマンドラインで指定する。置き場所の判断と、グローバルのuv設定（`~/.config/uv/uv.toml`）へ置かない理由は、
    `agent-toolkit:writing-standards`の`references/dependency-management.md`のパッケージ単位の除外の項に従う
- 文書lint（textlint、markdownlint、prettier）はpyfltr経由（miseタスク、prekのhook、CI）で行い、`AGENTS.md`を含む文書をpyfltrの対象集合でチェックする
  - `package.json`の`scripts`へ、pyfltrと別に文書lintを動かすスクリプト（`lint`、`lint:fix`など）を置かない。
    起動するコマンドごとに対象集合が分かれ、一方の結果だけでは他方の対象にある警告が漏れるためである
  - 新規Node系プロジェクトでも同じとする。pyfltrがlintツールを`node_modules/.bin/`から起動する設定では、`devDependencies`のlintパッケージは残す

## CI / リリース関連

- CI workflowのLinuxジョブはpyfltr公式イメージの`container:`実行を方針とし、
  container適用対象・キャッシュ方式の具体は各リポジトリの`.github/workflows/**`をSSOTとして揃える

以下4点はworkflow編集時の確認観点であり、実値は各リポジトリの`.github/workflows/**`に従う。

- container化ジョブではuv / pnpm / Node.js / miseのセットアップステップを省く。
  GitHub Actionsのピン留め確認には独立したstepを置かず、pyfltrの組み込みlinter`pinact`へ任せる。
  `pinact`は`pyproject.toml`の`[tool.pyfltr]`が持つ`preset = "latest"`で有効になり、CIの`ci.yaml`が実行する`pyfltr ci`と、push前に実行する`pyfltr run`・`pyfltr fast`（prekのpre-commitを含む）のいずれにも含まれるため、独立したstepは同じ確認の重複になる。
  Pythonバージョンマトリクスは
  `env: UV_PYTHON: ${{ matrix.python-version }}`で引き継ぐ。
  `defaults.run.shell: bash`の指定が必須（GitHub Actionsは`container:`のジョブで`shell`の指定が無い`run`を`sh`で実行するため）
- `release.yaml`の`GH_TOKEN`は`${{ github.token }}`を使う（推奨構文）
- `release.yaml`のCI待機は、対象コミットを指定してCIワークフロー（`ci.yaml`）の実行を直接照会し、
  その結論で判定する方式とする。`check-suites` APIの先頭suiteを判定に使う方式は、
  リリースワークフロー自身のsuiteを拾い、CIが成功していても待機がタイムアウトするため、前段の直接照会を用いる。
  各リポジトリが用いる照会コマンドは`.github/workflows/`配下をSSOTとし、本文には所在だけを書く
- `container:`実行ジョブのstepへ新しいコマンド呼び出しを追加する場合は、先行stepで導入されることを確認するか、
  ジョブが宣言する`image`上でそのコマンドの存在を確認する
  - どちらでも利用可能と確認できないコマンドは、呼び出す前に同じジョブで導入する
  - `ENTRYPOINT`がシェル以外のimageでは、
    `docker run --rm --entrypoint sh <image> -c 'command -v <command>'`でimage内の存在を確認する
