---
name: dotfiles-repo-layout
user-invocable: false
description: >
  dotfilesリポジトリで`.chezmoi-source/`配下と配布先（`~/.claude/`・`~/.codex/`・`~/.gemini/`・`~/.config/`）の対応を
  判定するとき、ファイルの削除と改名で`pytools/post_apply.py`の`_REMOVED_PATHS`と
  `setup_codex_links.py`の`_LINKS`を扱うとき、および dotfilesユーザー・agent-toolkitユーザー・全プロジェクト編集者・
  dotfiles編集者のどのロール向けのファイル群かを判定するときに起動する。
---

# dotfilesのロールと配置

本スキルは本リポジトリのファイル群とロールの対応と、配布元と配布先の対応を提供する。
変更した規範の自セッション適用とpickerが書く`プロジェクト規範の指定`は`dotfiles-norm-edit`が扱う。

## ロールとファイル群の対応

本リポジトリと配布物には複数のロールが関与する。ファイル群を編集する際は対象読者を意識する。

- dotfilesユーザー: chezmoiソース・`bin`・`pytools`等を自分の環境にインストールして使う人
- agent-toolkitユーザー: `agent-toolkit`プラグインをマーケットプレイス経由で使う人（dotfilesユーザー含む）
- dotfilesユーザーとagent-toolkitユーザーはどちらもエンドユーザーの部分集合であり、エージェントへ指示するユーザー（`agent-toolkit/rules/01-agent.md`「役割分担」）との兼任を妨げない
  - 配布ルール（`~/.claude/rules/agent-toolkit/`）も導入済み前提で記述してよい
- 全プロジェクト編集者: あらゆるプロジェクトで編集作業をするエージェント
  - 配布物（`agent-toolkit`本体・`~/.claude/rules/agent-toolkit/`配下）を実行時にロードする
- dotfiles編集者: 本リポジトリや`agent-toolkit`本体を修正するエージェント
  - 全プロジェクト編集者の対象に加え、リポジトリ直下の`.claude/`と`AGENTS.md`もロードする
   （Claude Codeは`CLAUDE.md`と`CLAUDE.local.md`が無いプロジェクトで`AGENTS.md`を直接読む。観測の内容は`docs/development/audit-records.md`の「プロジェクト指示のCLAUDE.mdアダプター：2026年9月26日」にある）

各ファイル群の対象読者と役割。

| ファイル群 | 対象読者 | 役割 |
| --- | --- | --- |
| `agent-toolkit/skills/`配下 | 全プロジェクト編集者 | スキルの指示本体 |
| `.chezmoi-source/dot_claude/`配下 | 全プロジェクト編集者・dotfilesユーザー | 常時自動ロードされる行動原則（dotfilesユーザーには配布先`~/.claude/`相当） |
| `.chezmoi-source/dot_codex/`配下 | 全プロジェクト編集者 | Codex向けのユーザー設定とClaude Code側原本へのリンク |
| `docs/guide/claude-code-guide.md` | agent-toolkitユーザー | プラグインの導入・更新手順 |
| `.claude/`（リポジトリ直下） | dotfiles編集者 | 本リポジトリ開発時のみ参照されるClaude Codeプロジェクト設定 |
| `AGENTS.md`（リポジトリ直下） | dotfiles編集者 | 本リポジトリの案内 |
| `pytools/`・`bin/`・`scripts/` | dotfilesユーザー・dotfiles編集者 | コマンドラインツールと開発スクリプト |

## ディレクトリ構造の注意

Claude Code/Codex設定ディレクトリが複数あり、取り違えは影響範囲の異なる障害につながる。指示の対象を必ず確認する。

本リポジトリの成果物はLinux・Windowsの複数マシンへ配布される。
設定・規範・ツールの変更は全環境への配布を前提として反映先を判定し、配布先を直接編集せず配布原本
（chezmoiソース・`share/`配下の`*_managed*.json`・agent-toolkitプラグイン）を編集する。
例外はユーザーが単一環境限定と明示した対象と、既存規範が環境限定と定めた成果物
（開発とCIだけで使う`scripts/`配下をLinux前提とする[architecture.md](../../../docs/development/architecture.md)の方針など）とする。
エンドユーザー環境で起動される`libexec/`配下はchezmoiで配布せず`~/dotfiles`の作業ツリーから実行するため、LinuxとWindowsの両方で動く書き方とする。
配布元と配布先の対応は次の列挙に従う。

- `.chezmoi-source/dot_claude/`: 配布元。chezmoiが`~/.claude/`にデプロイする（グローバルユーザー設定の原本）
- `~/.claude/`: デプロイ先。`chezmoi apply`で上書きされるため直接編集してはならない
  - ユーザーが「`~/.claude`の設定を変えて」と言った場合、実際に編集すべきは`.chezmoi-source/dot_claude/`
- `.claude/`（本リポジトリルート）: dotfilesリポジトリ自身のClaude Codeプロジェクト設定。配布対象外
  - Codex側でも明示検出させたい場合は`.agents/skills`を`.claude/skills`へのシンボリックリンクにする
- `.chezmoi-source/dot_codex/`: Codex配布元。`~/.codex/`へデプロイする
  - `AGENTS.md`はCodex向けアダプター。`agent-toolkit/share/rules-common.codex.md`、`.chezmoi-source/dot_claude/rules/myprojects-common.md`および`agent-toolkit/rules/`配下の常時規範から
    `scripts/sync_codex_agents.py`（`scripts/sync_generated_files.py`が起動する）が生成するため、変更は生成元へ行う（手動編集は生成差分で上書きされて消失する）
  - この生成物はClaude Codeの入れ子指示から`.claude/settings.json`の`claudeMdExcludes`で除外する
  - `setup_codex_links.py`が`~/.codex/`から`.chezmoi-source/dot_claude/`配下の共有スキルと`docs`の原本へリンクを生成する
    - リンクはLinux/macOSではシンボリックリンク、Windowsではディレクトリジャンクションとする
    - chezmoiの`symlink_`はWindowsで特権不足により失敗するため使わない
  - `agent-toolkit/rules/`は配布先で`atk-auto`の標識を付けた本文へ書き換えるためリンクせず、
    `sync_agent_toolkit_rules.py`が`~/.claude/rules/agent-toolkit/`と`~/.codex/agent-toolkit/rules/`へ同期する
  - `~/.codex/skills`にはグローバルに使うスキルだけを置く
- `.chezmoi-source/dot_gemini/`: Antigravity CLI向けの配布元。`~/.gemini/`へデプロイする（`GEMINI.md`と`antigravity-cli/skills/`）
- `.chezmoi-source/dot_config/`: XDG準拠ツール設定（`git`・`uv`・`pyfltr`等）の配布元
  - ユーザーが「`~/.config/<tool>`の設定を変えて」と言った場合、実際に編集すべきは`.chezmoi-source/dot_config/<tool>/`
- `.chezmoi-source/`配下のファイルを削除・改名した場合、配布先の除去は`pytools/post_apply.py`の`_REMOVED_PATHS`への追記で行う。
  chezmoi自身は配布先を自動削除しないためである。
  改名時は`_REMOVED_PATHS`の`~/.claude`欄（Codex側にもリンクがある対象は`~/.codex`欄も）へ
  旧パスを追記し、`setup_codex_links.py`の`_LINKS`マッピングを新名へ更新する。
  追記する項目には登録日を書き、登録日から6か月を過ぎた項目は表から外す（規則は`pytools/_internal/removal_registry.py`）
- `AGENTS.md`（本リポジトリルート）: dotfiles編集者向けの案内文書。Claude Code／Codex双方がここを読む
  - `CLAUDE.md`は置かず、ホスト共通で`AGENTS.md`に従う。手元に`CLAUDE.local.md`を置く場合は`atk setup-project`が追跡対象外のアダプターを置く
