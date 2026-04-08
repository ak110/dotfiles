# セキュリティ

## サプライチェーン保護

パッケージマネージャーに対するサプライチェーン攻撃を緩和するため、公開から一定期間が経っていないパッケージのインストールをブロックしている。
`chezmoi apply` / `update-dotfiles` 実行時にグローバルへ自動適用される。

| ツール                | 設定                             | スコープ                            |
|-----------------------|----------------------------------|-------------------------------------|
| uv (uvx含む)          | `exclude-newer = "1 day"`        | グローバル (`~/.config/uv/uv.toml`) |
| npm / pnpm (pnpx含む) | `minimum-release-age=1440` (1日) | グローバル (`~/.npmrc`)             |

CI (GitHub Actions) のランナーはクリーン環境のため `~/.npmrc` が存在しない。
`python-lint` ジョブの step で `~/.npmrc` を作成し同じ保護を効かせている。
リポジトリルートに `.npmrc` を置く案は採用していない。
`pnpm dlx` が実行時に一時ディレクトリへ `cwd` を切り替える仕様により、
`dlx` 経由の呼び出し (pyfltr の `pnpx`) には効かないため。
将来的には pyfltr 側で textlint 実行時の設定注入対策が入る予定。

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
