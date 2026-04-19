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

## claude-plans-viewer

`~/.claude/plans/*.md`をブラウザで一覧・閲覧するローカルHTTPビューア。
SSHポートフォワード越しに複数マシンから参照しやすいよう、
左ペインのfilter入力欄の上に`socket.gethostname()`で取得したホスト名を表示する。

### 自動起動（Windows）

`chezmoi apply`後処理の「claude-plans-viewer自動起動セットアップ」ステップが、
スタートアップフォルダーに起動用の`.cmd`を冪等に配置する。
配置先は`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\claude-plans-viewer.cmd`。
次回ログオン以降はWindowsが自動起動する。
post-apply実行時にviewerが未起動であればバックグラウンドでも起動し、新規セットアップ直後でも即時利用できる状態にする。

`update-dotfiles`の再インストールタイミングでは、PowerShell側がviewerを一時停止してから
`uv tool install`を実行し、完了後に再起動する。
これによりviewerの`.exe`やvenv内ファイルがロックされた状態での再インストール部分失敗を防ぐ。

Linux/macOSでは自動起動セットアップはno-op。
常駐させる場合はユーザー側でコマンドを直接起動する。

### 環境別設定（環境変数）

ホスト・ポート・ルートディレクトリは環境変数で上書きできる。
優先順位は高い順に、CLI引数・環境変数・組み込み既定値。

| 環境変数 | 対応オプション | 既定値 |
| --- | --- | --- |
| `CLAUDE_PLANS_VIEWER_ROOT` | `--root` | `~/.claude/plans` |
| `CLAUDE_PLANS_VIEWER_HOST` | `--host` | `127.0.0.1` |
| `CLAUDE_PLANS_VIEWER_PORT` | `--port` | Windows: `28875` ／ その他: `28765` |

Windowsでユーザースコープに永続化する例:

```cmd
setx CLAUDE_PLANS_VIEWER_PORT 12345
```

`setx`で設定した値は新規起動するコマンドプロンプトから見える。
自動起動用の`.cmd`も新規プロセスから起動されるため、次回ログオン以降は最新値を継承する。

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
