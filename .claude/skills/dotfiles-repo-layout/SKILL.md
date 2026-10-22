---
name: dotfiles-repo-layout
user-invocable: false
description: >
  dotfilesリポジトリで`.chezmoi-source/`配下と配布先（`~/.claude/`・`~/.codex/`・`~/.gemini/`・`~/.config/`）の対応を
  判定するとき、ファイルの削除と改名で`pytools/post_apply.py`の`_REMOVED_PATHS`と
  `setup_codex_links.py`の`_LINKS`を扱うとき、および dotfilesユーザー・agent-toolkitユーザー・agent-basicsユーザー・全プロジェクト編集者・
  dotfiles編集者のどのロール向けのファイル群かを判定するときに起動する。
---

# dotfilesのロールと配置

本スキルは本リポジトリのファイル群とロールの対応と、配布元と配布先の対応を提供する。
変更した規範の自セッション適用とメインが書く`プロジェクト規範の指定`は`dotfiles-norm-edit`が扱う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../dotfiles-norm-edit/SKILL.md` | 判断 | `read:*/skills/dotfiles-norm-edit/SKILL.md` |

## ロールとファイル群の対応

ファイル群を編集する際は対象読者を意識する。

- dotfilesユーザー: chezmoiソース・`bin`・`pytools`等を自分の環境にインストールして使う人
- agent-toolkitユーザー: `agent-toolkit`プラグインをマーケットプレイス経由で使う人（dotfilesユーザーを含む）
- agent-basicsユーザー: `agent-basics`の品質スキルを使う人（agent-toolkitユーザーとdotfilesユーザーを含む）
- 各ロールはエンドユーザーの部分集合であり、エージェントへ指示するユーザーを兼ねてよい。agent-toolkitの配布ルールを導入済みとする前提は、agent-toolkitユーザーへの案内に限る
- 全プロジェクト編集者: あらゆるプロジェクトで編集作業をするエージェント。配布物（`agent-toolkit`本体・`~/.claude/rules/agent-toolkit/`配下）を実行時にロードする
- dotfiles編集者: 本リポジトリや`agent-toolkit`本体を修正するエージェント。加えてリポジトリ直下の`.claude/`と`AGENTS.md`をロードする。Claude Codeは`CLAUDE.md`と`CLAUDE.local.md`が無いプロジェクトで`AGENTS.md`を直接読む（観測は`docs/development/audit-records.md`「プロジェクト指示のCLAUDE.mdアダプター：2026年9月26日」）

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

## ディレクトリ構造の注意

Claude Code/Codexの設定ディレクトリが複数あり、取り違えると影響範囲の異なる障害になるため、指示の対象を確かめる。

成果物はLinux・Windowsの複数マシンへ配布される。設定・規範・ツールの変更は全環境への配布を前提に反映先を決め、配布先を直接編集せず配布原本（chezmoiソース・`share/`配下の`*_managed*.json`・agent-toolkitプラグイン）を編集する。例外はユーザーが単一環境限定と明示した対象と、既存規範が環境限定と定めた成果物（開発とCIだけで使う`scripts/`をLinux前提とする[architecture.md](../../../docs/development/architecture.md)の方針など）である。
`libexec/`配下はchezmoiで配布せず`~/dotfiles`の作業ツリーからエンドユーザー環境で実行するため、LinuxとWindowsの両方で動く書き方とする。

- `.chezmoi-source/dot_claude/`: chezmoiが`~/.claude/`へデプロイする配布元。`~/.claude/`は`chezmoi apply`で上書きされるため直接編集しない（「`~/.claude`の設定を変えて」は`.chezmoi-source/dot_claude/`の編集を指す）
- `.claude/`（リポジトリルート）: 本リポジトリ自身のClaude Codeプロジェクト設定で、配布対象外。Codexにも検出させる場合は`.agents/skills`を`.claude/skills`へのシンボリックリンクにする
- `.chezmoi-source/dot_codex/`: `~/.codex/`への配布元
  - `AGENTS.md`はCodex向けアダプターで、`scripts/sync_codex_agents.py`（`scripts/sync_generated_files.py`が起動）が生成するため、変更は生成元へ行う。生成元は`agent-toolkit/share/rules-common.codex.md`、`.chezmoi-source/dot_claude/rules/myprojects-common.md`、`agent-toolkit/rules/`配下の常時規範である。この生成物は`.claude/settings.json`の`claudeMdExcludes`でClaude Codeの入れ子指示から除外する
  - `setup_codex_links.py`が`~/.codex/`から`.chezmoi-source/dot_claude/`配下の共有スキルと`docs`の原本へリンクを作成する。リンクはLinux/macOSではシンボリックリンク、Windowsではディレクトリジャンクションとする（chezmoiの`symlink_`はWindowsで特権不足により失敗する）
  - `agent-toolkit/rules/`は配布先で`atk-auto`の標識付きの本文へ書き換えるため、リンクではなく`sync_agent_toolkit_rules.py`が`~/.claude/rules/agent-toolkit/`と`~/.codex/agent-toolkit/rules/`へ同期する
  - `~/.codex/skills`にはグローバルに使うスキルだけを置く
- `.chezmoi-source/dot_gemini/`: 汎用コーディングエージェントとして使うAntigravity CLI向けに`~/.gemini/`へ配布する（`GEMINI.md`と`antigravity-cli/skills/`）。共有プラグインは`agent-toolkit/`から`agent-toolkit-agy/`、`agent-basics/`から`agent-basics-agy/`へ独立して生成し、`post_apply`が`~/.gemini/config/plugins.json`の登録へ追加する。toolkitの生成rootにはスキル、agents、常時規範、6イベントのhook、共有MCPと実行資源を含める。basicsの生成rootは品質スキルだけで単独利用でき、元の名前空間と品質本文を保持する
- `.chezmoi-source/dot_config/`: XDG準拠ツール設定（`git`・`uv`・`pyfltr`等）の配布元（「`~/.config/<tool>`の設定を変えて」は`.chezmoi-source/dot_config/<tool>/`の編集を指す）
- `.chezmoi-source/`配下を削除・改名した場合、chezmoiは配布先を自動削除しない。`pytools/post_apply.py`の`_REMOVED_PATHS`の`~/.claude`欄（Codex側にもリンクがある対象は`~/.codex`欄も）へ旧パスを登録日付きで追記し、改名なら`setup_codex_links.py`の`_LINKS`を新名へ更新する。登録日から6か月を過ぎた項目は外す（規則は`pytools/_internal/removal_registry.py`）
- `AGENTS.md`（リポジトリルート）: dotfiles編集者向けの案内でClaude Code／Codex双方が読む。`CLAUDE.md`は置かず、ホスト共通で`AGENTS.md`に従う。手元に`CLAUDE.local.md`を置く場合は`atk setup-project`が追跡対象外のアダプターを置く
