# pytools（Pythonコマンドラインツール群）

`pytools/` ディレクトリに格納されたPythonパッケージ。
`chezmoi apply` 時に `uv tool install` で自動インストールされ、パスが通ったディレクトリに配置される。

## コマンド一覧

- `claude-commit` — claudeでコミットメッセージを生成してgit commitを実行
- `claude-plans-viewer` — `~/.claude/plans/*.md`をブラウザで一覧・閲覧するローカルHTTPビューア
- `claudize` — Claude Code設定ファイルの配布・同期
- `py-imageconverter` — 画像変換（リサイズ、フォーマット変換、メタデータ削除）
- `py-rename` — 正規表現でファイルリネーム
- `py-rmdirs` — 正規表現でディレクトリ削除
- `py-pdf-to-image` — PDFを画像に変換（要Poppler）
- `check-image-sizes` — 画像サイズの分布を分析
- `git-justify` — Gitコミット日時を営業時間内に調整
- `mvdir` — ディレクトリをマージ
- `update-ssh-config` — SSH config/authorized_keysを生成

## 手動インストール

```bash
uv tool install --editable ~/dotfiles
```

## 内部モジュール

`pytools/`直下で`_`始まりのモジュールは`chezmoi apply`の後処理から呼ばれる内部用で、コマンドラインからは直接使わない。

- `_install_claude_plugins.py` — `.claude-plugin/marketplace.json`をSSOTとして
  `agent-toolkit`プラグインを自動インストール・更新する。
  marketplace登録は`ak110/dotfiles`のGitHubショートハンドに統一しており、Claude Codeが慣例ディレクトリへ自動cloneする。
  `~/.claude/plugins/known_marketplaces.json`や`~/.claude/settings.json`の`extraKnownMarketplaces`に
  directory型・別repoなどの破損エントリが残っている場合は、GitHub型へ書き換えて修復する。
  CLI再登録で解消しないケースは同一ディレクトリ内の原子的置換で直接書き換え、
  続けて`marketplace update`でgit cloneを誘発する。
  この直接書き換え経路はClaude Code CLIが`settings.json.extraKnownMarketplaces`を更新しない
  既知不具合への回避策であり、CLI側で解消した際は削除候補となる。
  修復エントリを常時強制すると他ユーザー・他環境へ意図せず伝播する副作用が読めないため、
  `share/claude_settings_json_managed.*.json`には`extraKnownMarketplaces`を含めない方針とする。
  加えて、公式marketplaceの一部プラグイン（`_AUTO_DISABLED_PLUGIN_IDS`に列挙）は
  `claude plugin disable --scope user`で自動的に無効化する。
  逆に常時有効化したいプラグイン（`_AUTO_ENABLED_PLUGIN_IDS`に列挙、例: `context7`）は、
  未インストールなら`claude plugin install --scope user`で導入し、
  `enabledPlugins`で明示的に`false`のときだけ`claude plugin enable --scope user`で有効化する。
  いずれも状態変更は`claude`コマンド経由でのみ行い、`enabledPlugins`の直接書き換えはしない。
