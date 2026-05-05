# セキュリティ

## サプライチェーン保護

パッケージマネージャーに対するサプライチェーン攻撃を緩和するため、
公開から一定期間が経っていないパッケージのインストールをブロックする。
`chezmoi apply` / `update-dotfiles` 実行時にグローバルへ自動適用される。

| ツール                 | 設定                              | スコープ                              |
|------------------------|-----------------------------------|---------------------------------------|
| uv（uvx含む）          | `exclude-newer = "1 day"`         | グローバル（`~/.config/uv/uv.toml`）  |
| npm / pnpm（pnpx含む） | `minimum-release-age=1440`（1日） | グローバル（`~/.npmrc`）              |

一時的に無効化する場合は以下のコマンドを実行。

```bash
# uv
uv pip install --exclude-newer=0seconds <package>

# npm / pnpm
npm install --minimum-release-age=0 <package>
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
