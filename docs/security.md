# セキュリティ

## サプライチェーン保護

パッケージマネージャーに対するサプライチェーン攻撃を緩和するため、公開から一定期間が経っていないパッケージのインストールをブロックしている。
`chezmoi apply` / `update-dotfiles` 実行時にグローバルへ自動適用される。

| ツール                | 設定                             | スコープ                                                |
|-----------------------|----------------------------------|---------------------------------------------------------|
| uv (uvx含む)          | `exclude-newer = "1 day"`        | グローバル (`~/.config/uv/uv.toml`)                     |
| npm / pnpm (pnpx含む) | `minimum-release-age=1440` (1日) | グローバル (`~/.npmrc`) + 本リポジトリルートの `.npmrc` |

本リポジトリルートにも同値の `.npmrc` を置いている。
CI (GitHub Actions) 実行時はランナーがクリーン環境で `~/.npmrc` を持たないため、
リポジトリ配下の `pnpm` / `pnpx` 実行にも同じ保護を効かせる必要があるため。

なお `pnpm dlx` は実行時に専用の一時ディレクトリへ `cwd` を切り替えてから依存を解決する。
そのためリポジトリ直下の `.npmrc` は `dlx` 経由の呼び出し (pyfltr が使う `pnpx`) には適用されない。
この制約を回避するため、CI ワークフローでは `.npmrc` を `~/.npmrc` へコピーする step を
`python-lint` ジョブに含め、ユーザー設定として読み込ませている。

一時的に無効化したい場合（急ぎで最新版が必要な場合など）は、以下を参照。

```bash
# uv
uv pip install --exclude-newer 0seconds <package>

# npm / pnpm
npm install --minimum-release-age=0 <package>
pnpm install --config.minimum-release-age=0 <package>
```

### GitHub Actions ピン留め

GitHub Actions のアクションは [pinact](https://github.com/suzuki-shunsuke/pinact) でコミットハッシュにピン留めしている。
`make update` 実行時に自動更新される（mise 未導入時はスキップ）。
