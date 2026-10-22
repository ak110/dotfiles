# dotfiles

[![CI][ci-badge]][ci-url]

[ci-badge]: https://github.com/ak110/dotfiles/actions/workflows/ci.yaml/badge.svg
[ci-url]: https://github.com/ak110/dotfiles/actions/workflows/ci.yaml

[chezmoi](https://www.chezmoi.io/)で管理する個人用dotfilesである。

## 特徴

- chezmoi管理によるホーム配下（`~/.*`）の一括デプロイ
- LinuxとWindowsの両対応
- Python製コマンドラインツール群（`pytools`）の同梱
- Agent Plugins、Claude Code、Codexで同じルートを共有するagent-toolkitの配布
- サプライチェーン攻撃対策設定のグローバル適用（uv/npmの公開待機、`pinact`によるGitHub Actionsのコミットハッシュ固定）

agent-toolkitは、Agent Plugins、Claude Code、Codexで共有できるコーディングエージェント向けツールキットである。
単体導入にはClaude Code CLI、Codex CLI、uvを使用する。
dotfiles配布では、`chezmoi apply`後の処理がこれらの導入と更新を担う。
統合導入は[agent-toolkit導入ガイド](docs/guide/claude-code-guide.md)、
Codex固有の詳細は[Codex利用ガイド](docs/guide/codex-guide.md)を参照する。

## 前提条件

- [Git](https://git-scm.com/install/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [Node.jsとnpm](https://nodejs.org/ja/download)（fullモードでは後処理でも導入する）
- [PowerShell 7](https://learn.microsoft.com/ja-jp/powershell/scripting/install/installing-powershell-on-windows)（WindowsでのCodex CLI導入に推奨）

Windows PowerShellでは、環境によって公式インストーラーが使用する`Get-FileHash`を解決できず、Codex CLIの導入に失敗する。

agent-toolkitのGit呼び出しはGit 2.34.1以上に対応する。
Git 2.34.1が受理しない引数（`git worktree list -z`など）を使わない。worktree登録の列挙は共通実装`agent-toolkit/agent_toolkit/_git/worktrees.py`がGitの保存形式から読む。
CIの`pytest (3.15)`はUbuntu公式パッケージのGit 2.34.1を使い、通常のテストを実行する。
ローカルでは環境変数`AGENT_TOOLKIT_TEST_GIT`へGitの実行ファイルの絶対パスを指定して同じテストを実行できる。

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

## インストール

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

### miseの手動導入

`update-dotfiles`はmiseが見つからない場合にmise本体を自動で導入する。
ネットワーク制限などで自動導入に失敗した場合は、次の手順で導入してから`update-dotfiles`を再実行する。

Linuxの場合は以下を実行する。

```bash
curl -fsSL https://mise.run | MISE_INSTALL_PATH="$HOME/.local/bin/mise" sh
```

Windows（PowerShell）の場合は以下を実行する。

```powershell
winget install jdx.mise
```

## 設定（任意）

### GitHubトークンの設定（任意）

mise経由でインストールされる一部ツール（`jq`・`actionlint`・`pinact`など）では
aquaがGitHub artifact attestations検証のために`api.github.com`へアクセスする。
未認証では1時間あたり60リクエストのIPベース制限があり、`update-dotfiles`実行時に
`API rate limit exceeded`で失敗することがあり、OSを問わず発生し得る。

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

### Antigravity CLI

`update-dotfiles`はAntigravity CLI（`agy`）も導入する。
日本語の技術文書の推敲に使い、`agents_server`からはモデルを明示指定したときだけ選べる。

初回はブラウザでの認証を要する。`agy`を対話で起動して認証を済ませるまで、`agents_server`からは使えない。

```bash
agy
```

指定できるモデルスラッグは次のコマンドで確認する。

```bash
agy models
```

statuslineをClaude Codeと同じ体裁で表示する場合は、Antigravity CLIのstatuslineコマンドへ
`claude-statusline agy-statusline`を設定する。

### OpenCodeの委譲先

`update-dotfiles`は未導入のOpenCode 1を`npm install --global opencode-ai`で導入する。
導入に失敗した場合は警告の原因を解消して再実行し、`opencode --version`で確認する。
OpenCodeは`agents_server`の委譲先として使い、作業全体を統括するorchestratorには使わない。

Qwen3.8-27BなどのOpenAI互換エンドポイントは、[OpenCodeのprovider設定](https://opencode.ai/docs/providers/#custom-provider)へ定義する。
次は`opencode.json`の例であり、URLとモデルIDを実際の接続先へ合わせる。
認証が必要な場合はOpenCode側で設定する。

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "local": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "ローカルのQwen",
      "options": {"baseURL": "http://127.0.0.1:8000/v1"},
      "models": {"qwen3.8-27b": {"name": "Qwen3.8-27B"}}
    }
  }
}
```

agent-toolkit側では使う工程の候補列へ`opencode:<provider>/<model>[@<effort>]`を直接指定する。
モデル内の`/`を保持し、`@`より後をOpenCodeの`--variant`へ渡す。

```bash
atk config set review_model opencode:local/qwen3.8-27b,claude:sonnet/medium
atk config set chores_model opencode:local/qwen3.8-27b
```

OpenCodeは各工程へ直接指定でき、orchestrate_modelはClaude・Codex・agyの候補を使う。
5プリセットと7キーの対応は[工程別モデル設定](docs/guide/claude-code-guide.md#工程別モデル設定)を参照する。

### dotfilesプラグイン

リポジトリ直下の`plugins/<名前>/`に、自分用の配布物と後処理を追加できる。
`plugins/`配下はGitの追跡対象から外れる。

```text
plugins/
  my-settings/
    .chezmoi-source/
    post_apply.py
    .git/
```

各ファイルとディレクトリは任意である。
本体の適用と後処理を終えてから、プラグイン名の昇順に次を行う。

1. `.chezmoi-source/`があれば、その配布物をホームへ適用する
2. `post_apply.py`があれば、プラグインのディレクトリで`uv run --no-project --script`により実行する

本体と配布先が重なるファイルはプラグインの内容で上書きし、プラグイン名と配布先を警告する。
プラグイン同士では、名前順で後のものが優先する。
プラグインのテンプレートから本体の`.chezmoidata.toml`のデータは参照できない。

`update-dotfiles`は本体のpull後に、`.git`を持つプラグインも名前順にpullする。
プラグインのpullが失敗すると更新を停止する。
applyや`post_apply.py`の失敗は後処理の失敗として返し、他のプラグインの処理は継続する。
applyが失敗したプラグインの`post_apply.py`は実行しない。

## 導入モード

既定の`full`はAIエージェントと開発環境を導入する。
開発しないサーバーには`minimal`を指定できる。
初回導入では、取得した`install.sh`を`bash install.sh --mode minimal`で、Windowsでは`install.ps1 -Mode minimal`で実行する。

| 対象 | full | minimal |
| --- | --- | --- |
| Git・uv・chezmoi・mise、fzf・gh・glab・glow・jq・ripgrep、tmuxプラグイン、pytools | 導入 | 導入 |
| Claude Code・Codex・Antigravity・OpenCode・Gemini CLI・Herdr、プラグイン・MCP・statusline | 導入 | 導入を省く |
| dotnet・node・rust・gcloud・aws-cdk、開発用linter・formatter | 導入 | 導入を省く |

モードはagent-toolkitの設定ファイルの`dotfiles_mode`へ保存され、次回以降の更新にも適用される。
設定だけを変更する場合は、`atk config set dotfiles_mode minimal`を実行する。
`atk config get dotfiles_mode`で現在値を確認できる。
この2コマンドはAI用のPython依存環境が未準備でも使える。

```bash
update-dotfiles --mode minimal
update-dotfiles --prune
update-dotfiles --mode full
```

`--prune`は現在のモードで対象外の導入済みツールとキャッシュを削除する。
minimalでは共有ランタイム領域とAIエージェントの実体も対象となる。
Claude CodeとCodexの設定・会話履歴は保持する。
使用中のエージェントや旧Python環境を検出した場合は保持し、終了後の`update-dotfiles --prune`の再実行を案内する。
通常の更新では、共有Python依存環境と使用中の旧環境を削除しない。

## 使い方

```bash
update-dotfiles
```

更新が失敗したときや、直近の更新で何が実行されたかを確認したいときは、直近1回の実行の保存ログを表示する。

```bash
update-dotfiles logs
```

## ドキュメント

- [docs/index.md](docs/index.md): ドキュメントの総合案内
- [docs/guide/index.md](docs/guide/index.md): 利用者向け（Claude Code/Codex設定・SSH・セキュリティ）
- [docs/development/index.md](docs/development/index.md): 開発者向け
