---
name: dotfiles-repo-layout
user-invocable: false
description: >
  dotfilesリポジトリで`.chezmoi-source/`配下と配布先（`~/.claude/`、`~/.codex/`、`~/.gemini/`、`~/.config/`）の
  対応関係を判定するとき、ファイルの削除や改名に伴い`pytools/_internal/removed_paths.py`の`_REMOVED_PATHS`や
  `setup_codex_links.py`の`_LINKS`を更新するとき、および対象ファイルがどのロール向けのものかを判定するときに起動する。
---

# dotfilesのロールとファイル配置

本リポジトリのファイル群と読み手のロール、配布元と配布先の対応を定める。
エージェント向け規範をどの層・スキルへ置くかは`.claude/skills/edit-dotfiles-norms/references/norm-layer-map.md`が扱う。

## ロールとファイル群の対応

編集するファイルの読み手のロールに合わせて書く。
ロールはいずれもエンドユーザーの一部で、エージェントへ指示するユーザーを兼ねる場合がある。

- dotfilesユーザー: chezmoiのソース、`bin`、`pytools`などを自分の環境へ導入して使う。
- agent-toolkitユーザー: マーケットプレイスから`agent-toolkit`を導入して使う（dotfilesユーザーを含む）。
  agent-toolkitの配布ルールの導入を前提にする案内は、このロール向けに限る。
- agent-basicsユーザー: `agent-basics`の品質スキルを使う（上の2ロールを含む）。
- 全プロジェクト編集者: 配布された設定（`agent-toolkit`本体、`~/.claude/rules/agent-toolkit/`のルール）を読んで様々なプロジェクトを編集するコーディングエージェント。
- dotfiles編集者: 本リポジトリと`agent-toolkit`本体を変えるコーディングエージェント。
  全プロジェクト編集者の設定に加えて、リポジトリ直下の`.claude/`と`AGENTS.md`を読む。
  Claude Codeは`CLAUDE.md`と`CLAUDE.local.md`が無いプロジェクトでは`AGENTS.md`を直接読む。
  観測記録は`docs/development/audit-records-project-instructions.md`「プロジェクト指示のCLAUDE.mdアダプター：2026年9月26日」にある。

| ファイル群 | 対象読者 | 役割 |
| --- | --- | --- |
| `agent-basics/skills/`配下 | 品質スキルを適用する全プロジェクト編集者 | 成果物の品質基準 |
| `agent-toolkit/skills/`配下 | 全プロジェクト編集者 | スキルの指示本体 |
| `agent-basics/README.md` | agent-basicsユーザー | 品質スキルの単独導入と併用の案内 |
| `agent-toolkit/README.md` | agent-toolkitユーザー | ツールキットの概要・導入・初回操作の案内 |
| `.chezmoi-source/dot_claude/`配下 | 全プロジェクト編集者・dotfilesユーザー | 常時自動ロードされる行動原則（配布先`~/.claude/`） |
| `.chezmoi-source/dot_codex/`配下 | 全プロジェクト編集者 | Codex向けのユーザー設定とClaude Code側原本へのリンク |
| `docs/guide/claude-code-guide.md`・`docs/guide/codex-guide.md` | agent-toolkitユーザー・dotfilesユーザー | 詳細設定・操作とdotfiles固有の運用の案内 |
| `.claude/`（リポジトリ直下） | dotfiles編集者 | 本リポジトリ開発時のみ参照されるClaude Codeプロジェクト設定 |
| `AGENTS.md`（リポジトリ直下） | dotfiles編集者 | 本リポジトリの案内 |
| `pytools/`・`bin/`・`scripts/` | dotfilesユーザー・dotfiles編集者 | コマンドラインツールと開発スクリプト |

## ディレクトリ構造に関する注意点

Claude CodeとCodexの設定ディレクトリは複数あり、取り違えると影響範囲の違う不具合になるため、変更の指示がどのディレクトリを指すかを先に確かめる。
成果物はLinuxとWindowsの複数の環境へ配る。
配布先のファイルは直接編集せず、配布原本（chezmoiのソース、`share/`配下の`*_managed*.json`、agent-toolkitプラグインなど）を編集する。
ユーザーが単一の環境向けと明示した場合と、既存の規範が環境を限定している成果物（Linux前提の開発・CI用`scripts/`。`docs/development/architecture.md`）は例外とする。
`libexec/`配下はchezmoiで配らず、`~/dotfiles`の作業ツリーからエンドユーザーの環境で直接実行するため、LinuxとWindowsの両方で動くように書く。

- `.chezmoi-source/dot_claude/`: `~/.claude/`への配布元。
  `~/.claude/`配下は`chezmoi apply`で上書きされるため直接編集しない（「`~/.claude`の設定を変えて」はここの編集を指す）。
- `.claude/`（リポジトリ直下）: 本リポジトリ自身のClaude Codeのプロジェクト設定で、配布しない。
  Codexにも同じスキルを見せる時は`.agents/skills`から`.claude/skills`へのシンボリックリンクを作る。
- `.chezmoi-source/dot_codex/`: `~/.codex/`への配布元。
  - `AGENTS.md`はCodex用のアダプターで、`scripts/sync_codex_agents.py`（`scripts/sync_generated_files.py`から起動）が生成するため、生成元を変える。
    生成元は`agent-toolkit/share/rules/rules-common.codex.md`、`.chezmoi-source/dot_claude/rules/myprojects-common.md`、`agent-toolkit/share/rules/common/`配下の常時規範である。
    Claude Codeが入れ子で読まないよう、`.claude/settings.json`の`claudeMdExcludes`で除外している。
  - `setup_codex_links.py`は`~/.codex/`から`.chezmoi-source/dot_claude/`配下の共有スキルと`docs`の原本へのリンクを作る。
    LinuxとmacOSはシンボリックリンク、Windowsはディレクトリジャンクションにする（chezmoiの`symlink_`はWindowsで特権不足により失敗するため）。
  - `agent-toolkit/share/rules/common/`配下は配布先で`atk-auto`タグ付きの本文へ変換する。
    そのためリンクではなく`sync_agent_toolkit_rules.py`で`~/.claude/rules/agent-toolkit/`と`~/.codex/agent-toolkit/rules/`へ同期する。
  - `~/.codex/skills`には全体で使うスキルだけを置く。
- `.chezmoi-source/dot_gemini/`: Antigravity CLI向けに`~/.gemini/`へ配るファイル群（`GEMINI.md`、`antigravity-cli/skills/`など）。
  共有プラグインは`agent-toolkit/`から`agent-toolkit-agy/`へ、`agent-basics/`から`agent-basics-agy/`へ独立して生成し、`post_apply`が`~/.gemini/config/plugins.json`の登録へ加える。
  toolkitの生成ルートはスキル、agents、常時規範、6つのイベントフック、共有MCP、実行リソースを含む。
  basicsの生成ルートは品質スキルだけで単独で使え、元の名前空間と品質基準の本文を保つ。
- `.chezmoi-source/dot_config/`: XDGに従う各種ツール設定（`git`、`uv`、`pyfltr`など）の配布元。
  「`~/.config/<tool>`の設定を変えて」は`.chezmoi-source/dot_config/<tool>/`の編集を指す。
- `.chezmoi-source/AppData/`: Windowsの`%APPDATA%`（`~/AppData/Roaming`）配下への配布元で、`.chezmoiignore`がWindows以外では除外する。
  LinuxとmacOSで`~/.config`、Windowsで`%APPDATA%`を読むツール（herdrなど）は、`dot_config/<tool>/`と`AppData/Roaming/<tool>/`の両方へ同じ本文を置き、Windowsでは`.config/<tool>`を除外する。
- `.chezmoi-source/`配下のファイルを削除・改名しても、chezmoiは配布先に残った古いファイルを自動では削除しない。
  削除した旧パスを`pytools/_internal/removed_paths.py`の`_REMOVED_PATHS`の`~/.claude`の項目（Codex側にリンクがあれば`~/.codex`の項目も）へ登録日とともに足す。
  改名では`setup_codex_links.py`の`_LINKS`も新しい名前に直す。
  登録日から6か月を過ぎた項目は一覧から削除する（運用は`pytools/_internal/removal_registry.py`）。
- `AGENTS.md`（リポジトリ直下）: dotfiles編集者向けの案内で、Claude CodeとCodexの両方が読む。
  本リポジトリには`CLAUDE.md`を置かず、ホスト共通で`AGENTS.md`に従う。
  ローカル用の`CLAUDE.local.md`を置く場合は、`atk setup-project`でGit追跡外のアダプターを置く。
