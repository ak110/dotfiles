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
ルールファイル（`~/.claude/rules/agent-toolkit/`）と併用される前提で、プラグインにルールファイルと同等内容を書かない。

## ファイル構成と参照方向

- `agent-toolkit/`配下: プラグイン（スキル・サブエージェント・フックスクリプト・marketplace記述）
- `agent-toolkit/rules/`配下: ルールファイル（`agent.md`が基本原則・運用方針・言語表現を単独で担う）
- `~/.claude/rules/agent-toolkit/`: ルールファイルの配布先（直接編集不可）

参照方向はdotfilesリポジトリ→プラグイン、およびプラグイン↔ルールファイルを許容する。
配置先は「いつコンテキストへ読み込ませたいか」で判断する。

- 常時自動ロードしたい一般指針（`completed`制約・並列点検・`run_in_background`既定など）はルールファイルへ置く
- 特定タスクでのみ必要な指針はスキル本体（`agent-toolkit/skills/<name>/SKILL.md`）に残す

## 配布物としての記述方針

配布先の利用者は本リポジトリのdotfiles利用者とは限らないため、手元プロジェクト固有の前提を断定的に書かない。

- 自指的な表現（「本リポジトリは〜」）・特定設定値の前提（「`shouldUsePoint: false`のため〜」）・特定ディレクトリ構成の前提を断定せず、異なり得る条件は条件付き表現（「`～`設定が有効な場合、」など）で書く
- 仕様参照としてのルール名・設定キー名・選択肢の説明は記述してよい
- 配布物（スキル・サブエージェント・hookスクリプト・コード）のdocstring・コメント・本文には配布物自身の挙動・仕様のみを記述し、利用者環境側の連携設計（個人フックとの優先順序など）は書かない
- 配布物の出力文字列・フックメッセージ・docstringにリポジトリ管理外の個人メモファイル名を含めない

スキル・サブエージェント編集時は次を守る。

- SKILL.md本体に必要な情報は本体に直接書く。`references/`から別の`references/`を多段参照させない
- スキル・サブエージェント間で記述が重複しても、読み込まれるコンテキストが異なるため無理に共通化しない
 （不適切な統合はコンテキスト汚染や指示漏れを招く）
- 並行する手順を別スキルに新設する際は、既存スキルの表記との整合を確認する
- 「実行時エラーで判明する仕様（tool quirk）」「具体例」は再発リスクと影響度を踏まえて保持判断する
  - 一過性で再発リスクの低いものは削除可
- agent-toolkit同梱スキル参照は`agent-toolkit:<skill-name>`形式に統一する
  - サブエージェント名（`plan-implementer`等）はAgentツールの`subagent_type`引数表記に揃えプレフィックス無しを維持する
  - スキルのリネームに限らず、当該参照表記を含む編集全般に適用する
  - プロジェクトローカルスキル（dotfilesリポジトリ直下`.claude/skills/`配下に置く`agent-toolkit-edit`自身など）はプラグイン同梱ではないため、プラグイン接頭辞を付けず素のスキル名で参照する

## スキル間の連携

`agent-toolkit:plan-mode`から作業を開始し、作成した計画ファイルは`ExitPlanMode`を合意ゲートとして通過して
`agent-toolkit:plan-impl`スキルへ引き継ぐ。
計画ファイルの`## 実行方法`のレビューステップに「レビューは実施しない」とあればレビュー工程をスキップし、
それ以外は記載のスキル・エージェント（既定は`agent-toolkit:careful-review`スキル）へ引き継ぐ。
レビューは`plan-spec-reviewer`と`plan-impl-reviewer`を全体差分対象に並列起動する。
指摘があればメインが統合し、修正再実装・コミット統合・再レビューを指摘解消まで繰り返す。

## バージョン更新

### SSOTの2ファイル

`version`／`description`を以下2箇所で完全に同一文字列に保つ。

- `agent-toolkit/.claude-plugin/plugin.json`
- `.claude-plugin/marketplace.json`の`plugins[]`内`name == "agent-toolkit"`のエントリ

整合性は`agent-toolkit/scripts/pretooluse_test.py`の`TestManifestSsot`が検査し、`uvx pyfltr run`で自動的に失敗する。
`scripts/agent_toolkit_bump.py`はdotfilesリポジトリ直下の`scripts/`配下を指す（`agent-toolkit/scripts/`とは別）。

### 判定基準

利用者に届く振る舞いが変わるものは必ずbumpする。

- bumpが必要: フックスクリプト・entry pointロジック変更／checkの追加・削除／`hooks/hooks.json`の`matcher`・`command`変更／依存・実行環境要件の変更／allowlistなどブロック条件の変更
- bumpが不要: コメント・docstringのみ／`*_test.py`のみ／入出力が不変なリファクタリング／誤字・スタイル調整

判断に迷う場合はbumpする（pre-1.0であれば頻繁にMINORを更新しても問題ない）。種別の使い分けは次の通り。

`git commit`時に`agent-toolkit/`配下の変更を含みつつ`plugin.json`の`version`未変更の場合、
`agent-toolkit/scripts/pretooluse.py`の検知フックが`warn`を返す。bump不要に該当する場合は警告を無視して進める。

- PATCH（`+0.0.1`）: 軽微な修正（メッセージ変更、スタイル調整、バグ修正、検出漏れの修正など）
- MINOR（`+0.1.0`）: 機能追加・検出範囲の大幅拡大・descriptionが変わる規模など、規模の大きい変更に限定
- MAJOR（`+1.0.0`）: ユーザーからの明示的な指示がない限り行わない

### 未プッシュ範囲での統合

未プッシュコミットが既に1回以上bumpを含む場合、後続編集ごとに追加でbumpしない。
`scripts/agent_toolkit_bump.py`は既存bumpと同等以下の指定をno-op扱いするため、追加実行しても結果は変わらない。
既存bumpがPATCHで後続編集がMINOR相当なら`agent_toolkit_bump.py minor`で既存bumpを上書き格上げする。

レビュー時の判定: 計画でMINOR bumpを宣言していても当該コミット単体ではversion変更が無いケースがある。
未プッシュコミット範囲（計画着手前から`HEAD`まで）に同等以上のbumpが含まれていれば対応済み相当として扱う。
差分単体ではなくpush前の累積状態で判定する。

### plan modeでの取り扱い

計画フェーズではbump要否や既存bumpとの差分を調査しない。
種別（PATCH／MINOR／MAJOR）のみ`### エージェント判断`へ記述し、具体的なversion数値（`1.1.0→1.2.0`等）は書かない
（実装時に既存の未プッシュbumpへ統合されると計画文面の数値が実体と乖離するため）。実装フェーズで
`scripts/agent_toolkit_bump.py {種別}`を実行する。ツール側が既存bumpとの統合を吸収するため`git log`確認は不要。
計画ファイルに具体的な数値が記述されていても、既存の未プッシュbumpへ吸収済みの場合は計画適合とみなす。
検証ではversion数値の一致を確認せず、bump種別の整合のみを確認する。

## 同期先ドキュメント

### docs/guide/claude-code-guide.md

`docs/guide/claude-code-guide.md`の「agent-toolkit」セクションに各プラグインのチェック内容要約がある。
以下の変更時は当該セクションも併せて更新する（軽微な閾値調整やパターン追加など要約が変わらない範囲は不要）。

- 新しいcheckの追加・既存checkの削除
- 検出範囲の大きな変更（allowlist／blocklistの方針変更）
- 依存ツールの変更（`uv`以外を要求するようになった等）
- 新しいプラグインを追加した場合（セクション追加が必要）

### install-claude.sh / install-claude.ps1

`install-claude.sh`の`FILES`と`install-claude.ps1`の`$files`、および`agent-toolkit/rules/`配下のmdファイル一覧の3者は
完全一致を保つ。mdファイルを追加・削除した際は両スクリプトの配列要素を同じ内容に手動同期する。
整合性は`agent-toolkit/scripts/install_script_ssot_test.py`が検査し、`uvx pyfltr run`で自動的に失敗する。
GitHub API非依存で動かす方針のため自動同期手段は持たない。

## セッション状態フラグ

`agent-toolkit`プラグインのフックは、セッション単位の状態ファイルを介してPreToolUseとPostToolUse間で情報を共有する。
パスは`{tempdir}/claude-agent-toolkit-{session_id}.json`とする。
規則の一般論は`agent-toolkit/skills/agent-standards/references/claude-hooks.md`の「セッション状態ファイル」節に従う。
フラグを追加・変更する際は本表を更新し、書き込み元と読み取り元の対応関係を保つ。

- `test_executed` — PostToolUse(Bash)が記録
  - PreToolUse(Bash)の`git commit`未検証警告の抑制に使う
- `git_status_checked` — PostToolUse(Bash)が`git status` / `git log` / `git diff`を観測して記録
- `git_log_checked` — PostToolUse(Bash)が`git log`を観測して記録
  - PreToolUse(Bash)のamend / rebase直前確認に使う
  - commit / rebase / push / ファイル編集を観測した時点でリセットする
- `plan_mode_skill_invoked` — PostToolUse(Skill)が`agent-toolkit:plan-mode`呼び出しを観測して記録
  - PostToolUseのplan file形式検査の有効化、およびPreToolUseの最初ツール警告の抑制に使う
- `agent_toolkit_edit_skill_invoked` — dotfiles個人フックPostToolUse(Skill)が`agent-toolkit-edit`呼び出しを観測して記録
  - dotfiles個人フックPreToolUseが`agent-toolkit/`配下を編集する際の、未起動時の警告抑制に使う
  - 書き込み元・読み取り元ともdotfiles個人フック（`scripts/claude_hook_*.py`）。配布プラグインは関与しない

## 編集手順

push前にbumpが必須（同じバージョンでは`claude plugin update`が「最新です」と返し利用者へ配信されないため）。

1. 「バージョン更新」の判定基準に該当する場合は`scripts/agent_toolkit_bump.py {patch|minor|major}`を実行する
2. `description`を変更する場合はSSOTの2ファイルを手で同期する
3. 必要なら`docs/guide/claude-code-guide.md`のチェック内容リストを更新する
4. `uvx pyfltr run-for-agent`を実行し、SSOTテストを含む全テストがgreenであることを確認する
5. 変更をコミットする

## フック実装の配置先（個人フックと配布物）

PreToolUseフックの配置先は2系統。汎用機能はプラグインへ、dotfiles固有の前提に依存する機能は個人フックへ配置する。
類似チェックが既に片方に存在する場合はそちらへ統合する（SSOT原則）。判断に迷う場合はユーザーへ確認する。

- `scripts/claude_hook_pretooluse.py`（個人フック）: chezmoi経由で自分の`~/.claude/settings.json`にのみマージされる
  - dotfiles固有の運用前提（`~/.claude/`がchezmoi配布先、個人の命名規約など）に依存するチェック向け
- `agent-toolkit/`（プラグイン）: `.claude-plugin/marketplace.json`経由で他者にも配布される
  - 汎用的な制約・自動化（一般的な文字化け検出、PowerShell互換性チェックなど）向け

プラグインに配置した場合は「バージョン更新」節の手順に従う。
個人フックに配置した場合は`share/claude_settings_json_managed.posix.json`および同`win32.json`の
`matcher`に新しいツール名を追加する必要があるか確認する。

agent-toolkit配下の編集時、dotfiles固有名の混入を`scripts/claude_hook_pretooluse.py`の専用チェックがブロックする。
ブロック対象の個人プロジェクト名固定リストは当該スクリプト内で定義する。
スキル名・pytoolsコマンド名・scripts名はhook実行時に当該ディレクトリをスキャンして動的取得するため手動更新は不要。
OSSとして公開しているプロジェクト名はwarning通知に留める。

## 複数hook共存時の識別子

agent-toolkitのhookが利用者環境の他hookと同一イベントで共存する場合がある。
自身のhookメッセージを他hookから判別するため、`[auto-generated: agent-toolkit/<hook>]`形式のプレフィックスを行頭に置く。
プレフィックス・サフィックスの規約は`agent-toolkit/skills/agent-standards/references/claude-hooks.md`の
「コーディングエージェント宛てメッセージの標識」節に従う。
reason本文の冒頭文字列で判別する経路は採用しない（本文の改変に対して安定しないため）。

## marketplace管理

`update-dotfiles`（`chezmoi apply`後処理）は`pytools/_internal/install_claude_plugins.py`経由で
agent-toolkitプラグインを自動インストール・更新する。marketplace配布は2段階の構成。

- bootstrap経路: `install-claude.sh`/`install-claude.ps1`がGitHub型として登録する
- chezmoi apply経路: 後処理がdirectory型（dotfilesリポジトリの絶対パス直接参照）で維持し、GitHub型登録が残存する環境では自動でdirectory型へマイグレーションする

### ローカル編集の反映ワークフロー

`agent-toolkit/`配下を編集したときの反映手順（chezmoi管理下）:

1. `agent-toolkit/`配下のファイルを編集する
2. `chezmoi apply`（または`update-dotfiles`）を実行する
3. Claude Codeを再起動するか`/reload-plugins`を実行する

version bumpは不要。編集が即時反映される。

## コミットメッセージ方針と.gitmessage

`agent-toolkit:commit`スキルのコミットメッセージ方針と`.gitmessage`は配布範囲が異なるため意図的に重複させる。SSOT化しない。
