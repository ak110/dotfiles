# pytools（Pythonコマンドラインツール群）

`pytools/` ディレクトリに格納されたPythonパッケージ。
`chezmoi apply` 時に `uv tool install` で自動インストールされ、パスが通ったディレクトリに配置される。

## コマンド一覧

- `claude-commit` — claudeでコミットメッセージを生成してgit commitを実行
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
