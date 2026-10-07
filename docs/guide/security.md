# セキュリティ

## サプライチェーン保護

パッケージマネージャーに対するサプライチェーン攻撃を緩和するため、
公開から一定期間が経っていないパッケージのインストールをブロックする。
`chezmoi apply` / `update-dotfiles` 実行時にグローバルへ自動適用される。

| ツール | 設定 | スコープ |
| ------------------------ | ----------------------------------- | --------------------------------------- |
| uv（uvx含む） | `exclude-newer = "1 day"` | グローバル（`~/.config/uv/uv.toml`） |
| npm（npx含む） | `min-release-age=1`（日数。1日） | グローバル（`~/.npmrc`） |
| pnpm（pnpx含む） | `minimum-release-age=1440`（分。1日） | pnpmのグローバル設定（`pnpm config set --location global`） |
| mise | `minimum_release_age = "168h"`（7日） | グローバル（`~/.config/mise/config.toml`） |

npmとpnpmは公開待機のキー名と単位が異なるため、設定先を分けている。
pnpmのグローバル設定は、`update-dotfiles`の実行時にpnpmが導入済みの場合だけ設定される。

一時的に無効化する場合は以下のコマンドを実行。

```bash
# uv
uv pip install --exclude-newer=0seconds <package>

# npm
npm install --min-release-age=0 <package>

# pnpm
pnpm install --config.minimum-release-age=0 <package>
```
