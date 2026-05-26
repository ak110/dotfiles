---
name: agent-toolkit-edit
description: >
  `agent-toolkit/`配下のプラグイン（スキル・サブエージェント・フックスクリプト・marketplace記述）、
  `agent-toolkit/rules/`配下のルールファイル（配布先`~/.claude/rules/agent-toolkit/`）、
  `.claude-plugin/marketplace.json`を編集するときに使う。
  「agent-toolkit編集」「version bump」「marketplace管理」「セッション状態フラグ」などのキーワードでも起動する。
---

# agent-toolkit (Claude Codeルールファイル + プラグイン)

`agent-toolkit/`配下はClaude Codeプラグイン`agent-toolkit`として配布する。
ルールファイル（`~/.claude/rules/agent-toolkit/`）と併用される前提で、
プラグインにルールファイルと同等内容を書かない。

## ファイル構成と参照方向

編集対象と配置先は次の通り。

- `agent-toolkit/`配下: プラグイン（スキル・サブエージェント・フックスクリプト・marketplace記述）
- `agent-toolkit/rules/`配下: ルールファイル（`agent.md`が基本原則・運用方針・言語表現を単独で担う）。
  Codex向けの読み替えは`.chezmoi-source/dot_codex/AGENTS.md`内のマッピング表で扱う
- `~/.claude/rules/agent-toolkit/`: ルールファイルの配布先（直接編集不可）。
  - `agent.md`は常時自動ロードされ、デフォルトのシステムプロンプトやsuperpowersスキルの動作を上書きする位置付けとする

参照方向はdotfilesリポジトリ→プラグイン、およびプラグイン↔ルールファイルを許容する。

配置先は「いつコンテキストへ読み込ませたいか」で判断する。

- 常時自動ロードしたい一般指針（`completed`制約・並列点検・`run_in_background`既定など）はルールファイルへ置く
- 特定タスクでのみ必要な指針はスキル本体（`agent-toolkit/skills/<name>/SKILL.md`）に残す

## 配布物としての記述方針

配布先の利用者は本リポジトリのdotfiles利用者とは限らない。
執筆者の手元プロジェクト固有の前提を断定的に書かない。

- 「本リポジトリは〜」「本プロジェクトでは〜」のような自指的な表現を避ける
- 特定設定値の採用を前提にした断定（例:「`shouldUsePoint: false`に設定しているため〜」）を避ける
- 特定のディレクトリパス・ファイル構成を前提とした断定を避ける
- 配布先環境で異なる可能性のある条件は条件付き表現（「`～`設定が有効な場合、」など）で書く
- 仕様参照としてのルール名・設定キー名・選択肢の説明は記述してよい
- 配布物（プラグイン同梱のスキル・サブエージェント・hookスクリプト・コード）の
  docstring・コメント・本文には、配布物自身の挙動・仕様のみを記述する。
  利用者環境側のhook・スキル・連携設計（個人フックとの優先順序、利用者独自スキルとの呼び分けなど）は書かない。
  配布先の利用者環境はdotfiles環境と一致しないため、断定的な連携設計は誤誘導を招く

配布物の出力文字列・フックメッセージ・docstringにはリポジトリ管理外の個人メモファイル名を含めない。
検出対象は`scripts/claude_hook_pretooluse.py`の項目3が定義する。

スキル・サブエージェント編集時は次を守る。

- SKILL.md本体に必要な情報は本体に直接書く。`references/`から別の`references/`を多段参照させない
- サブエージェントやスキルなどの間で記述が重複する場合があるが、
  読み込まれるコンテキストが異なる場合があるため、無理に共通化しない
  - 不適切に統合するとコンテキスト汚染や指示漏れを招く
- 並行する手順を別スキルに新設する際は、既存スキルの表記との整合を確認する
- 「実行時エラーで判明する仕様（tool quirk）」「具体例」は再発リスクと影響度を踏まえて保持判断する
 （一過性で再発リスクの低いものは削除可）
- agent-toolkit同梱スキル参照は`agent-toolkit:<skill-name>`形式のプレフィックスを付与して統一する。
  - サブエージェント名（`plan-implementer`等）はAgentツールの`subagent_type`引数表記に揃えてプレフィックス無しを維持する
  - スキルのリネームに限らず、当該参照表記を含む編集全般に本方針を適用する

## スキル間の連携

`agent-toolkit:spec-driven`が有効な場合は同スキルの誘導に従い、無効な場合は`agent-toolkit:plan-mode`から始める。
`agent-toolkit:plan-mode`が作成した計画ファイルは`ExitPlanMode`を合意ゲートとして通過し、
`agent-toolkit:plan-impl`スキルへ引き継ぐ。
計画ファイルの`## 実行方法`のレビューステップに「レビューは実施しない」と記載されている場合はレビュー工程をスキップする。
それ以外は記載のスキル・エージェントへ引き継いでレビューを実施する（既定は`agent-toolkit:careful-review`スキル）。
引き継ぎ時にコンテキストが途絶している前提で、計画ファイルが唯一の入力源として自立するよう漏れなく記述する。

```mermaid
flowchart TB
    SD["agent-toolkit:spec-driven スキル（任意）"]:::sd
    subgraph PM["agent-toolkit:plan-mode スキル"]
      P[計画ファイル作成<br/>整合性チェック・codexレビュー並列起動]
    end
    subgraph PI["agent-toolkit:plan-impl スキル"]
      direction TB
      T[plan-implementer または メイン直接<br/>実装・検証] --> CM[メインがコミット<br/>中間／単一]
      CM -->|レビュー実施しない指定| END[完了]
      CM -->|レビュー実施・全コミット完了| CR
    end
    subgraph CRS["agent-toolkit:careful-review スキル"]
      direction TB
      CR[plan-spec-reviewer 仕様適合性＋成果物間整合性<br/>plan-impl-reviewer コード・ドキュメント単体品質＋日本語表現<br/>新エージェント並列・全体差分対象]
      CR -->|指摘あり<br/>メインが統合| T2[plan-implementer または メイン直接<br/>修正再実装]
      T2 --> CFix[メインがコミット統合<br/>amend or 新規コミット]
      CFix --> R2[plan-spec-reviewer / plan-impl-reviewer<br/>修正で書き換えたファイル群を対象に新エージェント並列起動]
      R2 -->|指摘あり| T2
      CR -->|指摘なし| END
      R2 -->|指摘なし| END
    end
    SD -.->|作業テーマごとに誘導| PM
    PM -->|ExitPlanMode| PI
    User[ユーザー手動起動] -.->|/agent-toolkit:careful-review| CR

    classDef sd stroke-dasharray: 4 2
```

## バージョン更新

### SSOTの2ファイル

`version`／`description`を以下2箇所で完全に同一文字列に保つ。

- `agent-toolkit/.claude-plugin/plugin.json`
- `.claude-plugin/marketplace.json`の`plugins[]`内`name == "agent-toolkit"`のエントリ

整合性は`agent-toolkit/scripts/pretooluse_test.py`の`TestManifestSsot`が検査し、
`uvx pyfltr run`で自動的に失敗する。

本スキル内で参照する`scripts/agent_toolkit_bump.py`はdotfilesリポジトリ直下の
`scripts/`配下のスクリプトを指す（`agent-toolkit/scripts/`配下とは別）。

### 判定基準

利用者に届く振る舞いが変わるものは必ずbumpする。

- bumpが必要: フックスクリプト・entry pointロジック変更／checkの追加・削除／
  `hooks/hooks.json`の`matcher`・`command`変更／依存・実行環境要件の変更／allowlistなどブロック条件の変更
- bumpが不要: コメント・docstringのみ／`*_test.py`のみ／入出力が不変なリファクタリング／誤字・スタイル調整

判断に迷う場合はbumpする方針とする（pre-1.0であれば頻繁にMINORを更新しても問題ない）。

種別の使い分けは次の通り。

- PATCH（`+0.0.1`）: 軽微な修正（メッセージ変更、スタイル調整、バグ修正、検出漏れの修正など）
- MINOR（`+0.1.0`）: 機能追加、検出範囲の大幅拡大、descriptionが変わる規模の変更など、規模の大きい変更に限定
- MAJOR（`+1.0.0`）: ユーザーからの明示的な指示がない限り行わない

### 未プッシュ範囲での統合

未プッシュコミットが既に1回以上bumpを含む場合、後続編集ごとに追加でbumpしない。
`scripts/agent_toolkit_bump.py`は既存bumpと同等以下の指定をno-op扱いするため、追加実行しても結果は変わらない。
既存bumpがPATCHで後続編集がMINOR相当の場合は`agent_toolkit_bump.py minor`で既存bumpを上書き格上げする。

レビュー時の判定: 計画でMINOR bumpを宣言しているにもかかわらず、当該コミット単体ではversion変更が無いケースがある。
未プッシュコミット範囲（計画着手前から`HEAD`まで）に同等以上のbumpが既に含まれていれば、
当該指摘は対応済み相当として扱う。差分単体ではなくpush前の累積状態で判定する。

### plan modeでの取り扱い

計画フェーズではbump要否や既存bumpとの差分を調査しない。
種別（PATCH／MINOR／MAJOR）のみ`### エージェント判断`へ記述し、具体的なversion数値（`1.1.0→1.2.0`等）は書かない。
実装時に既存の未プッシュbumpへ統合されると計画文面の数値が実体と乖離するためである。
実装フェーズで`scripts/agent_toolkit_bump.py {種別}`を実行する。
ツール側で既存bumpとの統合を吸収するため、計画フェーズで`git log`を確認する必要はない。

## 同期先ドキュメント

### docs/guide/claude-code-guide.md

`docs/guide/claude-code-guide.md`の「agent-toolkit」セクションに各プラグインのチェック内容要約がある。
以下の変更時は当該セクションも併せて更新する。

- 新しいcheckの追加・既存checkの削除
- 検出範囲の大きな変更（allowlist／blocklistの方針変更）
- 依存ツールの変更（`uv`以外を要求するようになった等）
- 新しいプラグインを追加した場合（セクション追加が必要）

軽微な閾値調整やパターン追加など要約が変わらない範囲なら更新不要。

### install-claude.sh / install-claude.ps1

`install-claude.sh`の`FILES`と`install-claude.ps1`の`$files`、および
`agent-toolkit/rules/`配下のmdファイル一覧の3者は完全一致を保つ。
`agent-toolkit/rules/`配下のmdファイルを追加・削除した際は、両スクリプトの配列要素を同じ内容に手動同期する。
整合性は`agent-toolkit/scripts/install_script_ssot_test.py`が検査し、
`uvx pyfltr run`で自動的に失敗する。
ワンライナーインストーラーをGitHub API非依存で動かす方針のため自動同期手段は持たない。

## セッション状態フラグ

`agent-toolkit`プラグインのフックは、セッション単位の状態ファイルを介してPreToolUseとPostToolUse間で情報を共有する。
パスは`{tempdir}/claude-agent-toolkit-{session_id}.json`である。
パス規則の一般論は`agent-toolkit/skills/agent-standards/references/claude-hooks.md`の
「セッション状態ファイル」節に従う。

フラグを追加・変更する際は本表を更新し、書き込み元と読み取り元の対応関係を保つ。

- `test_executed` — PostToolUse(Bash)が記録。
  PreToolUse(Bash)の`git commit`未検証警告の抑制に使う
- `git_status_checked` — PostToolUse(Bash)が`git status` / `git log` / `git diff`を観測して記録
- `git_log_checked` — PostToolUse(Bash)が`git log`を観測して記録。
  - PreToolUse(Bash)のamend / rebase直前確認に使う
  - commit / rebase / push / ファイル編集を観測した時点でリセットする
- `plan_mode_skill_invoked` — PostToolUse(Skill)が`agent-toolkit:plan-mode`呼び出しを観測して記録。
  PostToolUseのplan file形式検査の有効化、およびPreToolUseの最初ツール警告の抑制に使う
- `plan_mode_warning_emitted` — PreToolUseが最初ツール警告を発火済みかを記録（1セッション1回限り）

## 編集手順

1. 今回の変更が「バージョン更新」の判定基準に該当するか判定する
2. 該当する場合は`scripts/agent_toolkit_bump.py {patch|minor|major}`を実行する。
   未プッシュの既存bumpが指定種別と同等以上ならツールは何もせず、指定種別が上位なら既存bumpを上書きして格上げする
3. `description`を変更する場合はSSOTの2ファイルを手で同期する
4. 必要なら`docs/guide/claude-code-guide.md`のチェック内容リストを更新する
5. `uvx pyfltr run-for-agent`を実行し、SSOTテストを含む全テストがgreenであることを確認する
6. 変更をコミットする（通常の編集と同じコミットに含めてよい）

push前にはbumpが必須である。
同じバージョンでは`claude plugin update`が「最新です」と返すため、bumpしないと利用者へ配信されない。

## フック実装の配置先（個人フックと配布物）

PreToolUseフックの配置先は2系統。汎用機能はプラグインへ、dotfiles固有の前提に依存する機能は個人フックへ配置する。
類似チェックが既に片方に存在する場合はそちらへ統合する（SSOT原則）。判断に迷う場合はユーザーへ確認する。

- `scripts/claude_hook_pretooluse.py`（個人フック）: chezmoi経由で自分の`~/.claude/settings.json`にのみマージされる。
  dotfiles固有の運用前提（`~/.claude/`がchezmoi配布先、個人の命名規約など）に依存するチェック向け
- `agent-toolkit/`（プラグイン）: `.claude-plugin/marketplace.json`経由で他者にも配布される。
  汎用的な制約・自動化（一般的な文字化け検出、PowerShell互換性チェックなど）向け

プラグインに配置した場合は本スキルの「バージョン更新」節の手順に従う。
個人フックに配置した場合は`share/claude_settings_json_managed.posix.json`および同`win32.json`の
`matcher`に新しいツール名を追加する必要があるか確認する。

agent-toolkit配下の編集時、dotfiles固有名の混入を`scripts/claude_hook_pretooluse.py`の専用チェックがブロックする。
ブロック対象の個人プロジェクト名固定リストは当該スクリプト内で定義する。
スキル名・pytoolsコマンド名・scripts名はhook実行時に当該ディレクトリをスキャンして動的に取得するため、
新規追加時の手動更新は不要。
OSSとして公開しているプロジェクト名はwarning通知に留める。

## 複数hook共存時の識別子

agent-toolkitのhookが利用者環境の他hookと同一イベントで共存する場合がある。
このとき自身のhookメッセージを他hook側から判別可能にするため、
`[auto-generated: agent-toolkit/<hook>]`形式のプレフィックスを行頭に置く。
プレフィックス・サフィックスの規約は
`agent-toolkit/skills/agent-standards/references/claude-hooks.md`の
「コーディングエージェント宛てメッセージの標識」節に従う。
reason本文の冒頭文字列で判別する経路は採用しない（本文の改変に対して安定しないため）。

## marketplace管理

`update-dotfiles`（`chezmoi apply`後処理）は`pytools/_internal/install_claude_plugins.py`経由で
agent-toolkitプラグインを自動インストール・更新する。marketplace配布は2段階の構成。

- bootstrap経路: `install-claude.sh`/`install-claude.ps1`がGitHub型として登録する
- chezmoi apply経路: 後処理がdirectory型（dotfilesリポジトリの絶対パス直接参照）で維持し、
  GitHub型登録が残存する環境では自動でdirectory型へマイグレーションする

directory型を採用する理由は、dotfilesで編集した内容がpush/updateサイクルを介さずに即時反映される点にある。

### ローカル編集の反映ワークフロー

`agent-toolkit/`配下を編集したときの反映手順（chezmoi管理下）:

1. `agent-toolkit/`配下のファイルを編集する
2. `chezmoi apply`（または`update-dotfiles`）を実行する
3. Claude Codeを再起動するか`/reload-plugins`を実行する

version bumpは不要。編集が即時反映される。

## コミットメッセージ方針と.gitmessage

`agent-toolkit/rules/agent.md`のコミットメッセージ方針と`.gitmessage`は配布範囲が異なるため意図的に重複させる。
SSOT化しない。
