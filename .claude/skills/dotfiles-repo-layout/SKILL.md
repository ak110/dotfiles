---
name: dotfiles-repo-layout
user-invocable: false
description: >
  dotfilesリポジトリで`.chezmoi-source/`配下と配布先（`~/.claude/`、`~/.codex/`、`~/.gemini/`、`~/.config/`）の
  対応関係を判定するとき、ファイルの削除や改名に伴い`pytools/post_apply.py`の`_REMOVED_PATHS`や
  `setup_codex_links.py`の`_LINKS`を更新するとき、および対象ファイルがどのロール向けのものかを判定するときに起動する。
---

# dotfilesのロールとファイル配置

このスキルは、本リポジトリにおけるファイル群と各ロールの対応関係、および配布元と配布先の対応関係をまとめたものである。
変更した規範を自身のセッションへ適用する手順や、メインエージェントが記述する`プロジェクト規範の指定`については`dotfiles-norm-edit`を参照する。

## 名指し参照の読込方法

本文で名指しした資料と読込表の資料は、条件が成立したらその判断や操作の前に自分で全文読み、指定箇所に注目してから、その基準で判断する。読込表の列の使い方は`agent-basics:writing-standards`の`references/quality-principles.md`「読込表の読み方」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../dotfiles-norm-edit/SKILL.md` | 判断 | `read:*/skills/dotfiles-norm-edit/SKILL.md` |
| 読込表と本文の名指し参照を読む前 | `agent-basics:writing-standards/references/quality-principles.md`の「読込表の読み方」 | 判断 | `section:agent-basics:*/skills/writing-standards/references/quality-principles.md#「読込表の読み方」` |

## ロールとファイル群の対応

各ファイルを編集する際は、その対象読者となるロールを意識して作業を進める。

- dotfilesユーザー: chezmoiのソースファイル、`bin`、`pytools`などを自身の環境にインストールして利用するユーザー。
- agent-toolkitユーザー: マーケットプレイス経由で`agent-toolkit`プラグインを導入して利用するユーザー（dotfilesユーザーも含まれる）。
- agent-basicsユーザー: `agent-basics`の品質管理スキルを利用するユーザー（agent-toolkitユーザーおよびdotfilesユーザーも含まれる）。
- 各ロールはいずれもエンドユーザーの一部であり、エージェントへ直接指示を出すユーザーと兼ねる場合がある。agent-toolkitの配布ルールが導入済みであることを前提とする案内は、agent-toolkitユーザー向けのものに限定する。
- 全プロジェクト編集者: さまざまなプロジェクトで編集作業するコーディングエージェント。配布された設定（`agent-toolkit`本体や`~/.claude/rules/agent-toolkit/`配下のルール）を実行時に読み込んで動作する。
- dotfiles編集者: 本リポジトリや`agent-toolkit`本体の変更を担当するコーディングエージェント。全プロジェクト編集者の設定に加えて、リポジトリ直下の`.claude/`や`AGENTS.md`を読み込む。なお、Claude Codeは`CLAUDE.md`や`CLAUDE.local.md`が存在しないプロジェクトにおいて`AGENTS.md`を直接読み込む仕様となっている。観測記録は`docs/development/audit-records.md`「プロジェクト指示のCLAUDE.mdアダプター：2026年9月26日」を参照する。

| ファイル群 | 対象読者 | 役割 |
| --- | --- | --- |
| `agent-basics/skills/`配下 | 品質スキルを適用する全プロジェクト編集者 | 成果物の品質基準 |
| `agent-toolkit/skills/`配下 | 全プロジェクト編集者 | スキルの指示本体 |
| `agent-basics/README.md` | agent-basicsユーザー | 品質スキルの単独導入と併用の案内 |
| `.chezmoi-source/dot_claude/`配下 | 全プロジェクト編集者・dotfilesユーザー | 常時自動ロードされる行動原則（配布先`~/.claude/`） |
| `.chezmoi-source/dot_codex/`配下 | 全プロジェクト編集者 | Codex向けのユーザー設定とClaude Code側原本へのリンク |
| `docs/guide/claude-code-guide.md` | agent-toolkitユーザー | プラグインの導入・更新手順 |
| `.claude/`（リポジトリ直下） | dotfiles編集者 | 本リポジトリ開発時のみ参照されるClaude Codeプロジェクト設定 |
| `AGENTS.md`（リポジトリ直下） | dotfiles編集者 | 本リポジトリの案内 |
| `pytools/`・`bin/`・`scripts/` | dotfilesユーザー・dotfiles編集者 | コマンドラインツールと開発スクリプト |

## ディレクトリ構造に関する注意点

Claude CodeやCodexの設定ディレクトリは複数存在し、取り違えると影響範囲の異なる不具合につながるため、変更指示がどのディレクトリを対象としているかを事前に必ず確認する。

作成した成果物は、LinuxとWindowsの複数の環境へ配布される。設定、規範、ツールの変更はすべての環境へ配布されることを前提に反映先を決定し、配布先のファイルを直接編集するのではなく、必ず配布原本（chezmoiのソースファイル、`share/`配下の`*_managed*.json`、agent-toolkitプラグインなど）を編集する。ただし、ユーザーが特定の単一環境向けであると明示した場合や、既存の規範で環境が限定されている成果物（たとえば開発やCIでのみ使用する`scripts/`をLinux前提とする[architecture.md](../../../docs/development/architecture.md)の方針など）は例外とする。
なお、`libexec/`配下のファイルはchezmoiで配布せず、`~/dotfiles`の作業ツリーからエンドユーザー環境で直接実行されるため、LinuxとWindowsの双方で正しく動作するように記述する。

- `.chezmoi-source/dot_claude/`: chezmoiによって`~/.claude/`へ展開される配布元のディレクトリ。`~/.claude/`配下のファイルは`chezmoi apply`によって上書きされるため、直接編集しない（「`~/.claude`の設定を変更して」という指示は、`.chezmoi-source/dot_claude/`の編集を意味する）。
- `.claude/`（リポジトリ直下）: 本リポジトリ自身のClaude Code用プロジェクト設定であり、外部への配布対象外である。Codexにも同じスキルを検出させる場合は、`.agents/skills`から`.claude/skills`へのシンボリックリンクを作成して対応する。
- `.chezmoi-source/dot_codex/`: `~/.codex/`への配布元ディレクトリ。
  - ここにある`AGENTS.md`はCodex用のアダプターファイルであり、`scripts/sync_codex_agents.py`（`scripts/sync_generated_files.py`から起動される）によって自動生成されるため、変更は生成元に対して行う。生成元のファイルは、`agent-toolkit/share/rules-common.codex.md`、`.chezmoi-source/dot_claude/rules/myprojects-common.md`、および`agent-toolkit/rules/`配下の常時規範である。なお、この生成ファイルは`.claude/settings.json`の`claudeMdExcludes`に設定されており、Claude Codeが入れ子で読み込まないように除外されている。
  - `setup_codex_links.py`は、`~/.codex/`から`.chezmoi-source/dot_claude/`配下にある共有スキルや`docs`の原本へのリンクを作成する。このリンクは、LinuxやmacOSではシンボリックリンク、Windowsではディレクトリジャンクションとして作成する（chezmoiの`symlink_`はWindows上で特権不足により失敗するためである）。
  - `agent-toolkit/rules/`配下のファイルは、配布先で`atk-auto`タグが付いた本文へと変換される。そのため、リンクではなく`sync_agent_toolkit_rules.py`を用いて`~/.claude/rules/agent-toolkit/`および`~/.codex/agent-toolkit/rules/`へ同期する。
  - `~/.codex/skills`には、グローバルに利用するスキルのみを配置する。
- `.chezmoi-source/dot_gemini/`: 汎用コーディングエージェントとして利用するAntigravity CLI向けに、`~/.gemini/`へ配布するファイル群（`GEMINI.md`や`antigravity-cli/skills/`など）。共有プラグインは、`agent-toolkit/`から`agent-toolkit-agy/`へ、`agent-basics/`から`agent-basics-agy/`へと独立して生成され、`post_apply`によって`~/.gemini/config/plugins.json`の登録情報へ追加される。toolkitの生成ルートにはスキル、agents、常時規範、6つのイベントフック、共有MCP、実行リソースが含まれる。basicsの生成ルートは品質管理スキルのみで構成され単独で利用可能であり、元の名前空間と品質基準の本文を維持している。
- `.chezmoi-source/dot_config/`: XDG仕様に準拠した各種ツール設定（`git`、`uv`、`pyfltr`など）の配布元ディレクトリ。「`~/.config/<tool>`の設定を変更して」という指示は、`.chezmoi-source/dot_config/<tool>/`の編集を意味する。
- `.chezmoi-source/`配下のファイルを削除または改名した場合、chezmoiは配布先に残った古いファイルを自動的には削除しない。そのため、`pytools/post_apply.py`の`_REMOVED_PATHS`にある`~/.claude`の項目（Codex側にもリンクがある場合は`~/.codex`の項目も含む）へ、削除した旧パスを登録日とともに追記する。改名の場合は`setup_codex_links.py`の`_LINKS`も新しい名前に更新する。登録日から6か月が経過した項目は一覧から削除する（運用ルールは`pytools/_internal/removal_registry.py`を参照）。
- `AGENTS.md`（リポジトリ直下）: dotfilesの編集者を対象とした案内文書であり、Claude CodeとCodexの双方が参照する。本リポジトリには`CLAUDE.md`を置かず、各ホスト共通で`AGENTS.md`に従う運用としている。ローカル環境用に`CLAUDE.local.md`を配置する場合は、`atk setup-project`を実行してGit追跡対象外のアダプターを配置する。
