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

### UV_FROZENによるロックファイル尊重

`UV_FROZEN=1`環境変数を常時有効化している。
`uv sync`/`uv run`が`uv.lock`を尊重して動作し、意図しない依存の再解決を防ぐ。
運用詳細は[docs/development/development.md](../development/development.md)の「サプライチェーン攻撃対策」節を参照。

### GitHub Actionsピン留め

GitHub Actionsのアクションはコミットハッシュにピン留めして実行する。
[pinact](https://github.com/suzuki-shunsuke/pinact)による自動管理が有効化されており、
`make update`実行時に自動更新される。
