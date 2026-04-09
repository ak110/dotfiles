# セキュリティ

## サプライチェーン保護

パッケージマネージャーに対するサプライチェーン攻撃を緩和するため、公開から一定期間が経っていないパッケージのインストールをブロックしている。
`chezmoi apply` / `update-dotfiles` 実行時にグローバルへ自動適用される。

| ツール                | 設定                             | スコープ                            |
|-----------------------|----------------------------------|-------------------------------------|
| uv (uvx含む)          | `exclude-newer = "1 day"`        | グローバル (`~/.config/uv/uv.toml`) |
| npm / pnpm (pnpx含む) | `minimum-release-age=1440` (1日) | グローバル (`~/.npmrc`)             |

一時的に無効化したい場合（急ぎで最新版が必要な場合など）は、以下を参照。

```bash
# uv
uv pip install --exclude-newer 0seconds <package>

# npm / pnpm
npm install --minimum-release-age=0 <package>
pnpm install --config.minimum-release-age=0 <package>
```

### GitHub Actions ピン留め

GitHub Actionsのアクションは [pinact](https://github.com/suzuki-shunsuke/pinact) でコミットハッシュにピン留めしている。
`make update` 実行時に自動更新される（mise未導入時はスキップ）。
