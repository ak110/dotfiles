---
name: dotfiles-repo-layout
user-invocable: false
description: >
  dotfilesリポジトリで`.chezmoi-source/`配下と配布先（`~/.claude/`・`~/.codex/`・`~/.gemini/`・`~/.config/`）の対応を
  判定するとき、ファイルの削除と改名で`pytools/post_apply.py`の`_REMOVED_PATHS`と
  `setup_codex_links.py`の`_LINKS`を扱うとき、dotfiles利用者・agent-toolkit利用者・全プロジェクト編集者・
  dotfiles編集者のどのロール向けのファイル群かを判定するとき、`agent-toolkit:process-wi`のpickerが
  AWIごとの`プロジェクト規範の指定`を書くとき、`AGENTS.md`・`agent-toolkit/rules/`・
  `agent-toolkit/skills/`・`agent-toolkit/share/`・`.claude/skills/`の規範をセッション内で変更してから
  自セッションへ適用するとき、規範を変更したセッションで会話圧縮の後に作業を続けるとき、
  および変更済みまたは新設したスキルを起動するときに起動する。
---

# dotfilesのロールと配置

本スキルは本リポジトリのファイル群とロールの対応、配布元と配布先の対応、および変更した規範を自セッションへ適用する契約を提供する。

## ロールとファイル群の対応

本リポジトリと配布物には複数のロールが関与する。ファイル群を編集する際は対象読者を意識する。

- dotfiles利用者: chezmoiソース・`bin`・`pytools`等を自分の環境にインストールして使う人
- agent-toolkit利用者: `agent-toolkit`プラグインをマーケットプレイス経由で使う人（dotfiles利用者含む）
  - 配布ルール（`~/.claude/rules/agent-toolkit/`）も導入済み前提で記述してよい
- 全プロジェクト編集者: あらゆるプロジェクトで編集作業をするコーディングエージェント
  - 配布物（`agent-toolkit`本体・`~/.claude/rules/agent-toolkit/`配下）を実行時にロードする
- dotfiles編集者: 本リポジトリや`agent-toolkit`本体を修正するコーディングエージェント
  - 全プロジェクト編集者の対象に加え、リポジトリ直下の`.claude/`と`AGENTS.md`もロードする
   （Claude Codeは`CLAUDE.md`と`CLAUDE.local.md`が無いプロジェクトで`AGENTS.md`を直接読む。観測の内容は`docs/development/audit-records.md`の「プロジェクト指示のCLAUDE.mdアダプター：2026年9月26日」にある）

各ファイル群の対象読者と役割。

| ファイル群 | 対象読者 | 役割 |
| --- | --- | --- |
| `agent-toolkit/skills/`配下 | 全プロジェクト編集者 | スキルの指示本体 |
| `.chezmoi-source/dot_claude/`配下 | 全プロジェクト編集者・dotfiles利用者 | 常時自動ロードされる行動原則（dotfiles利用者には配布先`~/.claude/`相当） |
| `.chezmoi-source/dot_codex/`配下 | 全プロジェクト編集者 | Codex向けのユーザー設定とClaude Code側原本へのリンク |
| `docs/guide/claude-code-guide.md` | agent-toolkit利用者 | プラグインの導入・更新手順 |
| `.claude/`（リポジトリ直下） | dotfiles編集者 | 本リポジトリ開発時のみ参照されるClaude Codeプロジェクト設定 |
| `AGENTS.md`（リポジトリ直下） | dotfiles編集者 | 本リポジトリの案内 |
| `pytools/`・`bin/`・`scripts/` | dotfiles利用者・dotfiles編集者 | コマンドラインツールと開発スクリプト |

## ディレクトリ構造の注意

Claude Code/Codex設定ディレクトリが複数あり、取り違えは影響範囲の異なる障害につながる。指示の対象を必ず確認する。

本リポジトリの成果物はLinux・Windowsの複数マシンへ配布される。
設定・規範・ツールの変更は全環境への配布を前提として反映先を判定し、配布先を直接編集せず配布原本
（chezmoiソース・`share/`配下の`*_managed*.json`・agent-toolkitプラグイン）を編集する。
例外はユーザーが単一環境限定と明示した対象と、既存規範が環境限定と定めた成果物
（`scripts/`配下をLinux前提とする[architecture.md](../../../docs/development/architecture.md)の方針など）とする。
配布元と配布先の対応は次の列挙に従う。

- `.chezmoi-source/dot_claude/`: 配布元。chezmoiが`~/.claude/`にデプロイする（グローバルユーザー設定の原本）
- `~/.claude/`: デプロイ先。`chezmoi apply`で上書きされるため直接編集してはならない
  - ユーザーが「`~/.claude`の設定を変えて」と言った場合、実際に編集すべきは`.chezmoi-source/dot_claude/`
- `.claude/`（本リポジトリルート）: dotfilesリポジトリ自身のClaude Codeプロジェクト設定。配布対象外
  - Codex側でも明示検出させたい場合は`.agents/skills`を`.claude/skills`へのシンボリックリンクにする
- `.chezmoi-source/dot_codex/`: Codex配布元。`~/.codex/`へデプロイする
  - `AGENTS.md`はCodex向けアダプター。`agent-toolkit/share/rules-main.codex.md`、`.chezmoi-source/dot_claude/rules/myprojects-common.md`および`agent-toolkit/rules/`配下の常時規範から
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
  旧パスを追記し、`setup_codex_links.py`の`_LINKS`マッピングを新名へ更新する
- `AGENTS.md`（本リポジトリルート）: dotfiles編集者向けの案内文書。Claude Code／Codex双方がここを読む
  - `CLAUDE.md`は置かず、ホスト共通で`AGENTS.md`に従う。手元に`CLAUDE.local.md`を置く場合は`atk setup-project`が追跡対象外のアダプターを置く

## 変更後の規範の自セッション適用

規範の改訂へ着手する前に、依頼またはAWIが引用する文面を、作業ツリー内のエージェント向け文書の現行本文から固定文字列で検索する。
確認対象は`AGENTS.md`、`agent-toolkit/rules/`・`agent-toolkit/skills/`・`agent-toolkit/share/`配下、`.claude/skills/`配下とし、
配布先の`~/.claude/`と`~/.codex/`は編集対象の特定に使わない。
引用した文面が存在しない場合は、同じ目的を持つ現行条文を作業ツリーの規範から特定し、その条文を反映先とする。
古い引用をそのまま置換対象にすると、更新済みの条文と競合する規定が別の箇所へ追加される。

本リポジトリでコーディングエージェント自身のふるまいを定める規範を変更する作業では、変更を確定した時点からそのセッションの以降の作業へ変更後の文面を適用する。
対象となる規範は、`AGENTS.md`、`agent-toolkit/rules/`・`agent-toolkit/skills/`・`agent-toolkit/share/`配下、`.claude/skills/`配下である。
エージェント向け文書はセッション開始時点の版が読み込まれており作業ツリーの変更は自動では反映されないため、変更を確定した主体が変更後の文面を自身の以降の判断へ適用し、影響する委譲先の委譲プロンプトへその文面を明示して渡す。

`Skill`ツールで起動したスキルと`${CLAUDE_PLUGIN_ROOT}`配下から読む文書は、セッション開始時に導入済みだった版の本文を返す。
セッション中に変更したスキルは変更前の本文を返し、新設したスキルは`Unknown skill`で起動に失敗する。
このため、セッション中に変更または新設した規範ファイルは、作業ツリーの該当ファイルを絶対パスで読む。
`Skill`ツールで起動した後に変更済みと分かった場合も、作業ツリー版を読み直して差分を以降の判断へ適用する。

会話圧縮の後は、変更後の文面がコンテキストから失われ、導入済みの版だけが手元に残る。
以降の工程を定めるスキルと参照文書のうち、そのセッションで変更したものを作業ツリー版で読み直してから次の判断へ進む。
変更したファイルは、そのセッションで作成したcommitの変更ファイルを前掲の対象規範へ限定して特定する。
`<役割名>.parent.md`と、その`起動対象:`にある`<役割名>.subagent.md`のいずれかを変更した場合は、両者を同一のplugin rootから原子的に適用する。同じplugin rootに両者がそろい委譲起動契約が成立すると確認できた版だけを後続の起動へ使い、確認できない場合は稼働開始時のplugin rootにある両者を使い続ける。作業ツリー側の`<役割名>.subagent.md`だけを旧版の`<役割名>.parent.md`と組み合わせると、必須入力名と委譲元の入力が異なる版を混在させるためである。
適用対象は実行主体が文書を読んで従える規範の文面に限り、フック、MCPサーバー、スクリプトおよび権限設定の変更は配布と再起動を経るまでそのセッションへ反映されないため対象から除く。
除いた対象のうち、委譲先が現行plugin rootから自ら解決して実行する資源の欠陥をそのセッションで是正した場合は、`agent-toolkit:delegation`の`references/base-contract.md`が定める`是正済み資源:`の行で作業ツリー側の絶対パスを委譲プロンプトへ渡す。
変更後の規範に従うとその作業を完遂できないと判明した場合は、規範どおり進めることより、その変更の設計見直しを優先する。

`agent-toolkit:process-wi`のセッションでは、選定工程のpickerが処理対象のAWIごとに`プロジェクト規範の指定`を書く。
`プロジェクト規範の指定`の受け渡し形式は`agent-toolkit/share/pick-wi.subagent.md`が定める。
本節の適用対象となる規範を変更するAWIには、その変更の対象ファイルのリポジトリ相対パスを書く。変更しないAWIは`なし`とする。
反映先に本リポジトリのエージェント向け文書を含むAWIには、その変更を確定する主体自身が計画の採否を確定する前に次を読む要求も書く。`docs/development/concepts.md`と`docs/development/incidents.md`は索引であり、全文を読む。加えて、索引の見出しのうち変更対象のファイル名、工程名または機能名を含む見出しがリンクする分割ファイルの節を読む。見出しで判定できない場合は、索引がリンクする分割ファイルを変更対象のファイル名と工程名で検索し、一致した節を読む。利用者が確定した方針の本文は分割ファイルにあり、索引の全文だけでは届かない。
対象かどうかの判定は、そのAWIが挙げる反映先のパスを`agent_toolkit._plan.structure`の`is_agent_doc_target_file`が真とするかで行う。
レーン担当は選定工程の`選定結果の出力先ファイル`から自レーンの`プロジェクト規範の指定`を読むため、メインの委譲プロンプトではその要求の再掲を省く（努力目標。再掲は重複になる）。
メインは`プロジェクト規範の指定`が`なし`以外である項目を担当するレーンの委譲プロンプトへ、その項目のファイル名と対象ファイルのパスを渡す。
そのレーンで規範の変更を確定した主体は、同じレーンの以降の委譲先の委譲プロンプトへ変更後の文面を明示して渡す。並行する他のレーンはその変更を統合前に取得できないため、レーンをまたぐ伝播を本節の適用範囲から除く。
