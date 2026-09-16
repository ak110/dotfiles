---
name: dotfiles-repo-layout
description: >
  dotfilesリポジトリで`.chezmoi-source/`配下と配布先（`~/.claude/`・`~/.codex/`・`~/.config/`）の対応を
  判定するとき、ファイルの削除と改名で`pytools/post_apply.py`の`_REMOVED_PATHS`と
  `setup_codex_links.py`の`_LINKS`を扱うとき、dotfiles利用者・agent-toolkit利用者・全プロジェクト編集者・
  dotfiles編集者のどのロール向けのファイル群かを判定するとき、`AGENTS.md`・`agent-toolkit/rules/`・
  `agent-toolkit/skills/`・`agent-toolkit/share/`・`.claude/skills/`の規範をセッション内で変更してから
  自セッションへ適用するときに起動する。
---

# dotfilesのロールと配置

本スキルは、本リポジトリのファイル群とロールの対応、配布元と配布先の対応、及び変更した規範を自セッションへ適用する契約を提供する。

## ロールとファイル群の対応

本リポジトリと配布物には複数のロールが関与する。ファイル群を編集する際は対象読者を意識する。

- dotfiles利用者: chezmoiソース・`bin`・`pytools`等を自分の環境にインストールして使う人
- agent-toolkit利用者: `agent-toolkit`プラグインをマーケットプレイス経由で使う人（dotfiles利用者含む）
  - 配布ルール（`~/.claude/rules/agent-toolkit/`）も導入済み前提で記述してよい
- 全プロジェクト編集者: あらゆるプロジェクトで編集作業をするコーディングエージェント
  - 配布物（`agent-toolkit`本体・`~/.claude/rules/agent-toolkit/`配下）を実行時にロードする
- dotfiles編集者: 本リポジトリや`agent-toolkit`本体を修正するコーディングエージェント
  - 全プロジェクト編集者の対象に加え、リポジトリ直下の`.claude/`と`AGENTS.md`もロードする
   （Claude Codeは`CLAUDE.md`経由のfile importで読む）

各ファイル群の対象読者と役割。

| ファイル群 | 対象読者 | 役割 |
| --- | --- | --- |
| `agent-toolkit/skills/`配下 | 全プロジェクト編集者 | スキルの指示本体 |
| `.chezmoi-source/dot_claude/`配下 | 全プロジェクト編集者・dotfiles利用者 | 常時自動ロードされる行動原則（dotfiles利用者には配布先`~/.claude/`相当） |
| `.chezmoi-source/dot_codex/`配下 | 全プロジェクト編集者 | Codex向けのユーザー設定とClaude Code側原本へのリンク |
| `docs/guide/claude-code-guide.md` | agent-toolkit利用者 | プラグインの導入・更新手順 |
| `.claude/`（リポジトリ直下） | dotfiles編集者 | 本リポジトリ開発時のみ参照されるClaude Codeプロジェクト設定 |
| `AGENTS.md`（リポジトリ直下） | dotfiles編集者 | 本リポジトリの入口。`CLAUDE.md`は`@AGENTS.md`importの1行アダプター |
| `pytools/`・`bin/`・`scripts/` | dotfiles利用者・dotfiles編集者 | コマンドラインツールと開発スクリプト |

## ディレクトリ構造の注意

Claude Code/Codex設定ディレクトリが複数あり、取り違えは影響範囲の異なる事故につながる。指示の対象を必ず確認する。

本リポジトリの成果物はLinux・Windowsの複数マシンへ配布される。
設定・規範・ツールの変更は全環境への配布を前提として反映先を判定し、配布先を直接編集せず配布原本
（chezmoiソース・`share/`配下のmanaged設定・agent-toolkitプラグイン）を編集する。
例外は、ユーザーが単一環境限定と明示した対象と、既存規範が環境限定と定めた成果物
（`scripts/`配下をLinux前提とする[architecture.md](../../../docs/development/architecture.md)の方針など）とする。
配布元と配布先の対応は次の列挙を正本とする。

- `.chezmoi-source/dot_claude/`: 配布元。chezmoiが`~/.claude/`にデプロイする（グローバルユーザー設定の原本）
- `~/.claude/`: デプロイ先。`chezmoi apply`で上書きされるため直接編集してはならない
  - ユーザーが「`~/.claude`の設定を変えて」と言った場合、実際に編集すべきは`.chezmoi-source/dot_claude/`
- `.claude/`（本リポジトリルート）: dotfilesリポジトリ自身のClaude Codeプロジェクト設定。配布対象外
  - Codex側でも明示検出させたい場合は`.agents/skills`を`.claude/skills`へのシンボリックリンクにする
- `.chezmoi-source/dot_codex/`: Codex配布元。`~/.codex/`へデプロイする
  - `AGENTS.md`はCodex向けアダプター。`agent-toolkit/share/rules-main.codex.md`、`.chezmoi-source/dot_claude/rules/myprojects-common.md`及び`agent-toolkit/rules/`配下の共有規範から
    `scripts/sync_codex_agents.py`（`scripts/sync_generated_files.py`が起動する）が生成するため、変更は生成元へ行う（手動編集は生成差分で上書きされて消失する）
  - 共有ルール・スキルは`setup_codex_links.py`が
    `.chezmoi-source/dot_claude/`または`agent-toolkit/`の原本へリンクを生成する
    （Linux/macOSはシンボリックリンク、Windowsはディレクトリジャンクション。
    chezmoiの`symlink_`はWindowsで特権不足により失敗するため未使用）
  - `~/.codex/skills`にはグローバルに使うスキルだけを置く
- `.chezmoi-source/dot_config/`: XDG準拠ツール設定（`git`・`uv`・`pyfltr`等）の配布元
  - ユーザーが「`~/.config/<tool>`の設定を変えて」と言った場合、実際に編集すべきは`.chezmoi-source/dot_config/<tool>/`
- `.chezmoi-source/`配下のファイルを削除・改名した場合、配布先の除去は`pytools/post_apply.py`の`_REMOVED_PATHS`への追記で行う。
  chezmoi自身は配布先を自動削除しないためである。
  改名時は`_REMOVED_PATHS`の`~/.claude`欄（Codex側にもリンクがある対象は`~/.codex`欄も）へ
  旧パスを追記し、`setup_codex_links.py`の`_LINKS`マッピングを新名へ更新する
- `AGENTS.md`（本リポジトリルート）: dotfiles編集者向けの入口。Claude Code／Codex双方がここを読む
  - `CLAUDE.md`は`@AGENTS.md`をimportする1行のみのアダプター

## 変更後の規範の自セッション適用

本リポジトリでコーディングエージェント自身のふるまいを定める規範を変更する作業では、変更を確定した時点からそのセッションの以降の作業へ変更後の文面を適用する。
対象となる規範は、`AGENTS.md`、`agent-toolkit/rules/`・`agent-toolkit/skills/`・`agent-toolkit/share/`配下、`.claude/skills/`配下である。
規範文書はセッション開始時点の版が読み込まれており作業ツリーの変更は自動では反映されないため、変更を確定した主体が変更後の文面を自身の以降の判断へ適用し、影響する委譲先の起動プロンプトへその文面を明示して渡す。
適用対象は実行主体が文書を読んで従える規範の文面に限り、フック、MCPサーバー、スクリプト及び権限設定の変更は配布と再起動を経るまでそのセッションへ反映されないため対象から除く。
除いた対象のうち、委譲先が現行plugin rootから自ら解決して実行する資源の欠陥をそのセッションで是正した場合は、`agent-toolkit:delegation`の`references/base-contract.md`が定める`是正済み資源:`の行でその資源の作業ツリー側の絶対パスを起動文へ渡す。
変更後の規範に従うとその作業を完遂できないと判明した場合は、規範どおり進めることより当該変更の設計の見直しを優先する。

`agent-toolkit:process-wi`のセッションでは、選定工程のpickerが処理対象のAWIごとに`project_notes`を書く。
`project_notes`の受け渡し形式は`agent-toolkit/share/pick-wi.subagent.md`が定める。
本節の適用対象となる規範を変更するAWIには、その変更の対象ファイルのリポジトリ相対パスを書く。変更しないAWIは`なし`とする。
反映先に本リポジトリのコーディングエージェント向け文書を含むAWIには、`docs/development/concepts.md`と`docs/development/incidents.md`を編集主体自身が同じセッションで全文読む要求も書く。
対象かどうかの判定は、そのAWIが挙げる反映先のパスを`agent_toolkit._plan.structure`の`is_agent_doc_target_file`が真とするかで行う。
レーン担当は選定工程の固定出力ファイルから自レーンの`project_notes`を読むため、メインの起動プロンプトへその要求を再掲しない。
メインは、`project_notes`が`なし`以外である項目を担当するレーンの起動プロンプトへ、その項目のファイル名と対象ファイルのパスを渡す。
そのレーンで規範の変更を確定した主体は、同じレーンの以降の委譲先の起動プロンプトへ変更後の文面を明示して渡す。並行する他のレーンはその変更を統合前に取得できないため、レーンをまたぐ伝播を本節の適用範囲から除く。
