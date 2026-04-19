# SSH先の`~/.claude/plans/`をWindowsから閲覧する

Windowsのブラウザから、SSH接続先Linuxにある`~/.claude/plans/*.md`を一覧・閲覧するための仕組み。
SSHポートフォワード越しに、リモートで起動したローカルHTTPサーバーをWindows側から参照する。
Markdownはサーバー側でHTMLに変換し、[share/vscode/markdown.css](../../share/vscode/markdown.css)を適用する。

## 仕組み

- Linux側: `claude-plans-viewer`コマンドが`127.0.0.1:8765`で待ち受ける。
  Markdown→HTML変換には`markdown-it-py`を使い、raw HTMLをエスケープしてXSS経路を塞ぐ
- Windows側: `remote-plans USER@HOST`を実行するとSSHポートフォワードを張ってリモートビューアを起動し、
  既定ブラウザで`http://127.0.0.1:8765/`を開く
- Markdown→HTML変換はサーバー側で完結する。ブラウザ側のJavaScriptはリスト描画とHTML取得のみを担う

## 前提

- Linux（SSH接続先）に本dotfilesをセットアップ済みで、`chezmoi apply`により`claude-plans-viewer`が
  `~/.local/bin/`へ配置されていること
- WindowsのPATHにOpenSSHの`ssh.exe`が通っていること。Windows 10以降は既定で利用可能
- Windowsに本dotfilesをセットアップ済みで、`chezmoi apply`により`remote-plans.cmd`が`~/bin/`へ配置されていること

## 使い方

PowerShellまたはコマンドプロンプトで次を実行する。

```cmd
remote-plans USER@HOST
```

`USER@HOST`はSSH接続先を指す。`~/.ssh/config`のホスト別名でも構わない。
実行すると、SSHトンネルとリモート側のビューア起動、既定ブラウザでの表示まで自動で行う。

例:

```cmd
remote-plans aki@192.0.2.10
remote-plans my-server
```

### ポートを変更する

既定のポート`8765`が他のプロセスで使われている場合は、第2引数で別のポートを指定する。
ローカルとリモートで同じポート番号を使い回す。

```cmd
remote-plans USER@HOST 18765
```

### 終了方法

SSHを実行しているコマンドプロンプトで`Ctrl+C`を押すか、そのウィンドウを閉じる。
SSHセッションが切れるとリモート側のビューアも自動的に停止する。

## 操作

- 左ペインに`~/.claude/plans/`配下のMarkdownファイルを更新日時の降順で表示する
- 左上の入力欄でファイル名を部分一致でフィルタする
- ファイルを選択すると右ペインにMarkdownプレビューを表示する
- 初回は最新のファイルを自動で開く

## トラブルシューティング

### ブラウザが開いたがページが表示されない

3秒の起動待ちよりSSH接続とビューア起動に時間がかかった場合、ブラウザ側で更新（F5）すれば表示される。
それでも表示されない場合はSSH側のウィンドウのエラーメッセージを確認する。

### `bind: Address already in use`でSSHが失敗する

Windows側のポート`8765`が他のプロセスで使われている。
第2引数で別のポートを指定して再実行する。

### リモート側で`address already in use`が出る

リモート側のポート`8765`が他のプロセスで使われている。
別のポートを指定するか、リモート側で該当プロセスを確認する。

### `claude-plans-viewer: command not found`

リモート側で`~/.local/bin/claude-plans-viewer`が配置されていないか、本dotfiles導入後に`chezmoi apply`を
再実行していない可能性がある。リモートで次を実行してから再度試す。

```bash
update-dotfiles
```
