# セキュリティ

## サプライチェーン保護

パッケージマネージャーへのサプライチェーン攻撃を緩和するため、公開から一定期間が経っていないパッケージのインストールをブロックする。
`chezmoi apply`・`update-dotfiles`の実行時に、次の設定がグローバルへ自動で適用される。

| ツール | 設定 | 設定先 |
| ------------------------ | ----------------------------------- | --------------------------------------- |
| uv（uvx含む） | `exclude-newer = "1 day"` | `~/.config/uv/uv.toml` |
| npm（npx含む） | `min-release-age=1`（日数。1日） | `~/.npmrc` |
| pnpm（pnpx含む） | `minimum-release-age=1440`（分。1日） | pnpmのグローバル設定（pnpmが導入済みの場合だけ設定） |
| mise | `minimum_release_age = "168h"`（7日） | `~/.config/mise/config.toml` |

公開直後の版が必要な場合は、次のように一時的に無効化して実行する。

```bash
# uv
uv pip install --exclude-newer=0seconds <package>

# npm
npm install --min-release-age=0 <package>

# pnpm
pnpm install --config.minimum-release-age=0 <package>
```
