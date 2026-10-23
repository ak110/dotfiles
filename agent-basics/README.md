# agent-basics

コード・設計・テスト・レビュー・文章の品質基準を、作業に応じて読むスキルとして提供する。
基本の起動スキルは`agent-basics:writing-standards`と`agent-basics:review-standards`である。
原因分析は`agent-basics:bugfix`、検証の品質基準は`agent-basics:check-execution`が扱う。

## Claude Codeへの導入

```sh
claude plugin marketplace add ak110/dotfiles
claude plugin install agent-basics@ak110-dotfiles
```

プロジェクトの`.claude/settings.json`で`enabledPlugins`の`agent-basics@ak110-dotfiles`を`true`にする。
プロジェクト規範では、作成・修正時にwriting-standards、レビュー・指摘採否時にreview-standardsを起動する条件を明示する。
agent-toolkitを導入すると、同じマーケットプレイスのagent-basicsも依存として導入される。

## Codexへの導入

Codexのプラグイン画面で`ak110-dotfiles`マーケットプレイスの`agent-basics`を選び、プロジェクトで有効にする。
基本の起動スキルと読込表はClaude Codeと共通であり、ツール`Skill`を使えない環境では該当する`SKILL.md`を読む。
agent-toolkitと併用する場合は、Codexでは両プラグインを明示的に導入・更新する。

## Agent Plugins 1.0.0

`agent-basics/plugin.json`と同じディレクトリの`skills/`を、Agent Plugins対応ホストへ渡す。
manifestはClaude固有の依存宣言を持たず、独立パッケージとして利用できる。

## 単独導入のbootstrap

dotfilesリポジトリのrootにある導入スクリプトは、Claude CodeとCodexへagent-basicsだけを導入できる。
既存のtoolkit運用規範、MCP設定とatkラッパーを保持する。

```sh
bash ./install-claude.sh --basics-only
```

```powershell
pwsh -NoProfile -File ./install-claude.ps1 -BasicsOnly
```

Bash版の単独導入はJSONの読取りに既存のPython 3を使う。
PowerShell版はPowerShellのJSON処理を使う。
uvはtoolkitも導入する場合の前提である。

## 適用範囲

このプラグインが配るのはスキルと参照資料である。常時フック、MCP、ホームへの運用規範の配送は含まない。
認可・WI・委譲・セッション管理・完了報告には、利用するプロジェクトの手順を適用する。
agent-toolkitはこれらの運用を提供し、品質判断でagent-basicsを参照する。
