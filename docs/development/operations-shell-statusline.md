# 運用機能: 補完キャッシュとstatusline

## 対話シェル起動時のbash補完のキャッシュ

対話シェルは補完定義を得るためにコマンドごとの子プロセスを起動する。
起動回数が多く対話シェルの起動時間の大半を占めるため、生成結果をキャッシュへ保存して次回以降は読み込みだけで済ませる。
保存先は`${XDG_CACHE_HOME:-~/.cache}/dotfiles/bash-completion/<コマンド名>.bash`とする。
キャッシュは、存在しない場合と、生成元の実行ファイルがキャッシュより新しい場合に再生成する。
生成に失敗した場合はキャッシュを保存せず、そのコマンドの補完も読み込まずに続行する。
生成コマンドを変更した場合はキャッシュへ反映されないため、`~/.cache/dotfiles/bash-completion`を削除して再生成する。

## claude-statuslineの開発版導入

現在のブランチが`develop`で、`rust/claude-statusline/`に`origin/master`との差分がある場合は、解決済みのmiseから開発版をビルドする。
作業ツリーを解決できない場合、Git管理下でない場合、`develop`以外のブランチである場合、差分がない場合、
または開発版のビルドに必要なmiseが見つからない場合は、GitHub Releaseから取得する。
Gitの状態だけで開発ツリーと判定すると、ビルド手段を持たない環境で同じ状態が成立したときにstatuslineの導入そのものが止まるためである。
`develop`で`origin/master`を解決できない場合は取得へ切り替えず、後処理を失敗させる。
差分にはコミット済み、ステージ済み、未ステージの変更と、Gitのignore対象外である同じパス配下の未追跡ファイルを含める。
ビルド、成果物の読込、既存バイナリの原子的な置換に失敗した場合は、既存バイナリを保持したまま後処理を失敗させる。
開発版の導入失敗後も後続の後処理は継続し、最終終了コードは1とする。
リリース取得のネットワーク失敗は従来どおり非致命として扱う。

開発版へ置き換える前に、リリース取得用のETagを無効化する。
次回のリリース取得が`304 Not Modified`になっても、開発版を
リリース版として保持し続けないためである。

## claude-statusline配布バイナリのビルド来歴を検証する

GitHub Releaseから取得したバイナリが、公開タグのコミットからビルドされたことを検証できる。
`master` へのpushで動く `ci.yaml` がそのコミットをビルドして来歴を署名する。
`release-statusline.yaml` は成功した同じCI実行からバイナリを取得し、そのコミットに付けたタグのReleaseへ公開する。

GitHub CLIを `gh auth login` または `GH_TOKEN` で認証する。
dotfilesのcheckoutで、`tag` に検証するReleaseのタグを指定して実行する。

```bash
(
  set -e
  tag='statusline-vX.Y.Z'
  git fetch https://github.com/ak110/dotfiles.git "refs/tags/$tag"
  source_sha=$(git rev-parse --verify 'FETCH_HEAD^{commit}')
  gh release download "$tag" --repo ak110/dotfiles --pattern 'claude-statusline-x86_64-*'
  for asset in claude-statusline-x86_64-unknown-linux-gnu claude-statusline-x86_64-pc-windows-msvc.exe; do
    gh attestation verify "$asset" --repo ak110/dotfiles \
      --signer-workflow ak110/dotfiles/.github/workflows/ci.yaml \
      --source-digest "$source_sha"
  done
)
```

両方の検証が成功したことを出力で確かめる。
[リポジトリの証明一覧](https://github.com/ak110/dotfiles/attestations)も参照できる。
来歴証明を導入する前の版は対象外であり、`update-dotfiles` の取得処理は変更しない。
