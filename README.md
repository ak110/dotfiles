# dotfiles

[![CI][ci-badge]][ci-url]

[ci-badge]: https://github.com/ak110/dotfiles/actions/workflows/ci.yaml/badge.svg
[ci-url]: https://github.com/ak110/dotfiles/actions/workflows/ci.yaml

[chezmoi](https://www.chezmoi.io/)で管理する個人用dotfilesである。

## 特徴

- chezmoi管理によるホーム配下（`~/.*`）の一括デプロイ
- LinuxとWindowsの両対応
- Python製コマンドラインツール群（`pytools`）の同梱
- Claude CodeとCodexを組み合わせるagent-toolkitの配布
- サプライチェーン攻撃対策設定のグローバル適用（uv/npmの公開待機、`pinact`によるGitHub Actionsのコミットハッシュ固定）

## 前提条件

- [Git](https://git-scm.com/install/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [Node.jsとnpm](https://nodejs.org/ja/download)
- [PowerShell 7](https://learn.microsoft.com/ja-jp/powershell/scripting/install/installing-powershell-on-windows)（WindowsでのCodex CLI導入に推奨）

Windows PowerShellでは、環境によって公式インストーラーが使用する`Get-FileHash`を解決できず、Codex CLIの導入に失敗する。

### 例（Linux）

```bash
sudo apt install git
curl -fsSL https://astral.sh/uv/install.sh | sh
```

### 例（Windows）

```cmd
winget install --id=Git.Git -e --source=winget
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

agent-toolkitは、Claude CodeとCodexを組み合わせるコーディングエージェント向けツールキットである。
単体導入にはClaude Code CLI、Codex CLI、uvを使用する。
dotfiles配布では、Claude CodeとCodex CLIを`chezmoi apply`後の処理が公式インストーラーから導入し、更新する。
Codex CLIは公式スタンドアローン版を使用する。
統合導入は[agent-toolkit導入ガイド](docs/guide/claude-code-guide.md)、
Codex固有の詳細は[Codex利用ガイド](docs/guide/codex-guide.md)を参照する。

## インストール

### GitHubトークンの設定（任意）

mise経由でインストールされる一部ツール（`jq`・`actionlint`・`pinact`など）は、
aquaがGitHub artifact attestations検証のために`api.github.com`へアクセスする。
未認証では1時間あたり60リクエストのIPベース制限があり、`update-dotfiles`実行時に
`API rate limit exceeded`で失敗することがあり、OSに依存せず発生し得る。

回避には個人アクセストークンを発行し、ユーザー環境変数`GITHUB_TOKEN`へ設定する。

1. <https://github.com/settings/personal-access-tokens> へアクセスする
2. `Generate new token`をクリックする
3. `Repository access`は`Public Repositories (read-only)`を選択する（追加スコープ設定は不要）
4. `Generate token`をクリックしてトークンを発行する
5. 表示されたトークンをコピーする
6. ユーザー環境変数に登録する

   Linuxの場合は`~/.env`に追記する（`~/.bashrc`はchezmoiの管理対象で上書きされるため使わない）。

   ```bash
   export GITHUB_TOKEN=<コピーしたトークン>
   ```

   Windows（PowerShell）の場合は以下を実行する。

   ```powershell
   [Environment]::SetEnvironmentVariable("GITHUB_TOKEN", "<コピーしたトークン>", "User")
   ```

7. 新しいターミナルを開き直して`update-dotfiles`を再実行する

### Linux

```bash
curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install.sh | bash
```

### Windows（PowerShell）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/ak110/dotfiles/master/install.ps1 | iex"
```

#### 簡易版

```cmd
winget install twpayne.chezmoi && chezmoi init ak110 --source=%USERPROFILE%\dotfiles --apply && setx PATH "%PATH%;%USERPROFILE%\bin;%USERPROFILE%\.local\bin"
```

## 使い方

```bash
update-dotfiles
```

## ドキュメント

- [docs/index.md](docs/index.md): ドキュメント入口
- [docs/guide/index.md](docs/guide/index.md): 利用者向け（Claude Code/Codex設定・pytools・SSH・セキュリティ）
- [docs/development/development.md](docs/development/development.md): 開発者向け
