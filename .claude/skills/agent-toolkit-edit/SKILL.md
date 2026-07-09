---
name: agent-toolkit-edit
description: >
  `agent-toolkit/`配下のプラグイン（スキル・サブエージェント・フックスクリプト・marketplace記述）、
  `agent-toolkit/rules/`配下のルールファイル（配布先`~/.claude/rules/agent-toolkit/`）、
  `.claude-plugin/marketplace.json`を編集するときに使う。
  「agent-toolkit編集」「version bump」「marketplace管理」「セッション状態フラグ」などのキーワードでも起動する。
---

# agent-toolkit (Claude Codeルールファイル + プラグイン)

## ファイル構成と参照方向

- `agent-toolkit/`配下: プラグイン（スキル・サブエージェント・フックスクリプト・marketplace記述）
- `agent-toolkit/rules/`配下: ルールファイル
  （`01-agent.md`が基本原則・運用方針・言語表現を単独で担い、プラグイン側で同等内容を書かない）
- `~/.claude/rules/agent-toolkit/`: ルールファイルの配布先（直接編集不可）

参照方向はdotfilesリポジトリ→プラグイン、およびプラグイン↔ルールファイルを許容する。
配置先は「いつコンテキストへ読み込ませたいか」で判断する。

- 常時自動ロードしたい一般指針（`completed`制約・並列点検・`run_in_background`既定など）はルールファイルへ置く
- 特定タスクでのみ必要な指針はスキル本体（`agent-toolkit/skills/<name>/SKILL.md`）に残す

## 配布物としての記述方針

配布先の利用者は本リポジトリのdotfiles利用者とは限らないため、手元プロジェクト固有の前提を断定的に書かない。

- 自己言及的な表現（「本リポジトリは〜」）・特定設定値の前提（「`shouldUsePoint: false`のため〜」）・
  特定ディレクトリ構成の前提を断定せず、異なり得る条件は条件付き表現（「`～`設定が有効な場合、」など）で書く
- 仕様参照としてのルール名・設定キー名・選択肢の説明は記述してよい
- 配布物（スキル・サブエージェント・hookスクリプト・コード）のdocstring・コメント・本文には
  配布物自身の挙動・仕様のみを記述し、利用者環境側の連携設計（個人フックとの優先順序など）は書かない
- 配布物の出力文字列・フックメッセージ・docstringにリポジトリ管理外の個人メモファイル名を含めない
- 配布物内の記述が参照するSSOTは配布物内に配置する。
  dotfiles固有ファイル・非配布対象ファイル（`.claude/skills/`配下等）にSSOTを置き配布物から参照させない。
  dotfiles固有の運用ノウハウは配布物外のファイルに置いてよいが、配布物内の記述からの参照先とはしない
- 配布物文面は計画段階（plan modeの提案文・改訂案・例示文など）にも本節の方針を事前適用し、
  `## 変更内容`のdiff改訂後文面の確定前に+側文字列の固有名照合を実施して一般化表現へ置き換える。
  dotfiles固有名の混入を後段の機械検出（`scripts/claude_hook_pretooluse.py`）まで持ち越さない
  - 照合対象は同スクリプトの固有名ブロック対象（個人プロジェクト名固定リスト・pytools名・scripts名・スキル名）
- 配布物スキル本文でhook内部の実装挙動
  （ハッシュ照合・SHA256記録・ブロック機構・状態フラグ書き込み等）を
  説明する記述を書かない。
  利用者には挙動の観測結果（特定操作がブロックされる・警告が返る等）のみを提示する。
  - 禁止例: 「本スキル未起動のまま計画ファイルの編集に至った場合はPreToolUseフックがハッシュ照合でブロックする」
  - 許容例: 「本スキル未起動のまま計画ファイルの編集に至った場合はブロックされるため、
    本スキルを呼び出したうえで工程を実施する」
  - 例外: SSOT目的で状態フラグ一覧・hook間連携仕様を集約する節
    （`agent-toolkit:agent-standards`「セッション状態フラグ」節等）は本規定の対象外とする

スキル・サブエージェント編集時は次を守る。

- SKILL.md本体に必要な情報は本体に直接書く。`references/`から別の`references/`を多段参照させない
- スキル・サブエージェント間で記述が重複しても、読み込まれるコンテキストが異なるため無理に共通化しない
- 並行する手順を別スキルに新設する際は、既存スキルの表記との整合を確認する
- 「実行時エラーで判明する仕様（tool quirk）」「具体例」は再発リスクと影響度を踏まえて保持判断する
- agent-toolkit同梱スキル参照は`agent-toolkit:<skill-name>`形式に統一する
  - サブエージェント名（`plan-implementer`等）はAgentツールの`subagent_type`引数表記に揃えプレフィックス無しを維持する
  - プロジェクトローカルスキル（dotfilesリポジトリ直下`.claude/skills/`配下に置く`agent-toolkit-edit`自身など）は
    プラグイン同梱ではないため、プラグイン接頭辞を付けず素のスキル名で参照する
- 規範文言中でscope-escalation検出対象パターン（工程スキップ・作業省略・部分対応・規範違反承知の続行等）に
  言及する場合は肯定形で書き、否定形の生表記を本文へ含めない（例: 「登録された全工程を実施する」）
  - 否定形の生表記は`process-omission`カテゴリでヒットしEdit/Writeがブロックされる
  - 検出対象パターン一覧の典拠は`agent-toolkit/skills/agent-standards/references/scope-escalation-phrases.md`

## スキル間の連携

`agent-toolkit:plan-mode`から作業を開始し、作成した計画ファイルは`ExitPlanMode`を合意ゲートとして通過して
`agent-toolkit:plan-impl`スキルへ引き継ぐ。
計画ファイルの`## 実行方法`のレビューステップに「レビューは実施しない」とあればレビュー工程をスキップし、
それ以外は記載のスキル・エージェント（既定は`agent-toolkit:careful-review`スキル）へ引き継ぐ。
レビューは`plan-spec-reviewer`と`plan-impl-reviewer`を全体差分対象に並列起動する。
指摘があればメインが統合し、修正再実装・コミット統合・再レビューを指摘解消まで繰り返す。

## バージョン更新

本節のバージョン更新規定は`agent-toolkit/`配下（agent-toolkitプラグイン配布物）のみを対象とする。
`.chezmoi-source/`配下のchezmoi配布物・`bin/`配下のCLIラッパー・`scripts/`配下のヘルパースクリプトは
本規定の対象外とし、`agent_toolkit_bump.py`も更新しない。

`version`／`description`は以下の箇所で完全に同一文字列に保つ。

- `agent-toolkit/.claude-plugin/plugin.json`
- `.claude-plugin/marketplace.json`の`plugins[]`内`name == "agent-toolkit"`のエントリ

整合性は`agent-toolkit/scripts/pretooluse_test.py`の`TestManifestSsot`が検査し、`uvx pyfltr run`で自動的に失敗する。

### 判定基準

利用者に届く振る舞いが変わるものは必ずbumpし、判断に迷う場合はbumpする
（pre-1.0であれば頻繁にMINORを更新しても問題ない）。
`git commit`時に`agent-toolkit/`配下の変更を含みつつ`plugin.json`の`version`未変更の場合、
`agent-toolkit/scripts/pretooluse.py`の検知フックが`warn`を返す。bump不要に該当する場合は警告を無視して進める。
以下いずれにも該当しない場合はbumpしない
（例: コメント・docstringのみ／`*_test.py`のみ／入出力が不変なリファクタリング／誤字・スタイル調整）。

- PATCH（`+0.0.1`）: 軽微な修正（フックスクリプト・entry pointロジック変更／
  軽微な検出パターン追加・除外パターン追加（新規checkが検出範囲を大幅に広げる場合はMINOR）／
  `hooks/hooks.json`の`matcher`・`command`変更／依存・実行環境要件の変更／
  軽微なallowlist追加・削除（allowlist方針の抜本変更はMINOR）／
  スキル・ルールファイルへの数行〜1節規模の規範文追記・条件補強・例示追加／メッセージ変更／バグ修正／検出漏れの修正）。
  軽微／大幅の判定は次の観点で判断する。
  検出パターン追加時は影響を受ける利用者範囲（当該checkが検出する差分件数）で、
  allowlist変更時は方針の抜本変更に該当するかで判断する
- MINOR（`+0.1.0`）: 機能追加・検出範囲の大幅拡大・descriptionが変わる規模など、規模の大きい変更に限定
  （description文言変更・トリガーキーワード追加・節新設・単一ファイル内で複数節に跨る規範改訂）。
  複数ファイルへそれぞれ単一節分の追記をする変更は、各ファイル単位でPATCH判定の対象とする
- MAJOR（`+1.0.0`）: ユーザーからの明示的な指示がない限り行わない

### 未プッシュ範囲での統合

未プッシュコミットが既に1回以上bumpを含む場合、後続編集ごとに追加でbumpしない。
`scripts/agent_toolkit_bump.py`は既存bump以下の指定をno-op扱いするため、追加実行しても結果は変わらない。
既存bumpがPATCHで後続編集がMINOR相当なら`agent_toolkit_bump.py minor`で上書き格上げする。
レビュー判定も同様に、未プッシュコミット範囲（計画着手前から`HEAD`まで）の累積bumpで対応済みを判定し、
差分単体では判定しない。
計画でMINOR bumpを宣言していても当該コミット単体ではversion変更が無いケースがある。

### plan modeでの取り扱い

計画フェーズではbump要否や既存bumpとの差分を調査せず、種別（PATCH／MINOR／MAJOR）のみ
`### エージェント判断`へ記述する（具体的なversion数値は書かない）。
判定は計画段階で対象ファイル一覧と変更内容から目視照合し、
上記「判定基準」節に基づく種別選定根拠を`### エージェント判断`欄へ1行で記述する。
実装フェーズで`scripts/agent_toolkit_bump.py {種別}`を実行する。
既存bumpとの統合をツール側が吸収するため`git log`確認は不要で、検証はbump種別の整合のみ確認する。
`agent-toolkit/scripts/pretooluse.py`の`agent-toolkit/`配下変更検知フックが
`plugin.json`版未変更をwarnで返すことで実装後の補完照合が行われる。
計画ファイル本文の`## 実行方法`には、検証ステップの手前へ
`scripts/agent_toolkit_bump.py {patch|minor|major}`の実行ステップを必ず含める。
bump不要時のみ省略可とし、その旨を計画ファイル本文へ明示する。
version bumpを伴う計画では、`agent-toolkit/.claude-plugin/plugin.json`と
`.claude-plugin/marketplace.json`を`## 変更内容`の対象ファイル一覧へ必ず含める。

配布物プラグインで新規CLI・新規コマンド・新規ラッパースクリプトを公開する変更を対象とする計画では、
計画段階の対象ファイル一覧に利用者環境での疎通経路を含める。
対象経路はインストールスクリプト・post-apply処理・PATH配置手法・bash補完登録・Windowsペアファイル同期を指す。
判断基準は「プラグイン単体利用者が特別な手動セットアップなしで新CLIを起動できるか」とする。
「特別な手動セットアップ」はPATH追加・環境変数の設定以外に、利用者が個別に行う追加設定を指す。
具体例は動的解決ラッパー配置・補完スクリプト手動リンク作成・パス手動指定などとする。
成立しない場合はインストールスクリプトへの追加ステップを対象ファイル一覧へ含める。

配布物プラグインが`bin/`配下でCLIを提供する場合、
実配置先は`~/.claude/plugins/cache/<marketplace>/<plugin-name>/<version>/bin/<cli>`となる。
`<version>`は更新ごとに変わる。
dotfiles配布利用者は`.chezmoi-source/dot_bashrc`のPATH追加で吸収する。
プラグイン単体利用者は`install-claude.sh`/`install-claude.ps1`側で動的解決ラッパーを`~/.local/bin/<cli>`に配置する。
ラッパーは`ls -d ~/.claude/plugins/cache/*/<plugin-name>/*/bin | sort -V | tail -1`で最新バージョンを実行時解決する。

## 同期先ドキュメント

- `docs/guide/claude-code-guide.md`「agent-toolkit」セクションのチェック内容要約は、要約が変わる変更時に併せて更新する
  - 新しいcheckの追加・既存checkの削除、検出範囲の大きな変更（allowlist／blocklistの方針変更）
  - 依存ツールの変更（`uv`以外を要求するようになった等）、新しいプラグイン追加時（セクション追加が必要）
- `install-claude.sh`の`FILES`と`install-claude.ps1`の`$files`、`agent-toolkit/rules/`配下のmdファイル一覧は
  完全一致を保ち、追加・削除時は両スクリプトを手動同期する。
  整合性は`agent-toolkit/scripts/install_script_ssot_test.py`が検査するが、自動同期手段は持たない
- 配布物スキル本体の外部インターフェース（判定区分・出力フォーマット・後始末コマンド分岐・サマリー表現など）へ
  新規追加・削除・改名を加える場合は連携整合を保つ。
  対象例は`apply-feedback`・`plan-mode`・`plan-impl`・`careful-review`など呼び出し元スキルを持ちうる配布物
  - 既知の呼び出し元スキル群（`.chezmoi-source/dot_claude/skills/`配下および他プロジェクトのスキル）を
    `grep -rn`で洗い出し、連携先の対応記述を同一計画内で同時更新する

## セッション状態フラグ

`agent-toolkit`プラグインが定義する全フラグ一覧のSSOTは`agent-toolkit:agent-standards`スキル本体（SKILL.md）
「セッション状態フラグ」節に置く。フラグを追加・変更する際は当該節を更新する。

## 編集手順

push前にbumpが必須（同じバージョンでは`claude plugin update`が「最新です」と返し利用者へ配信されないため）。

1. 「バージョン更新」の判定基準に該当する場合は`scripts/agent_toolkit_bump.py {patch|minor|major}`を実行する
2. `description`を変更する場合はSSOTの2ファイルを手で同期する
3. 必要なら`docs/guide/claude-code-guide.md`のチェック内容リストを更新する
4. `uvx pyfltr run-for-agent`を実行し、SSOTテストを含む全テストがgreenであることを確認する
5. 変更をコミットする

## フック実装の配置先（個人フックと配布物）

PreToolUseフックの配置先は複数ある。汎用機能はプラグインへ、dotfiles固有の前提に依存する機能は個人フックへ配置する。
類似チェックが既に片方に存在する場合はそちらへ統合する（SSOT原則）。判断に迷う場合はユーザーへ確認する。

- `scripts/claude_hook_pretooluse.py`（個人フック）: chezmoi経由で自分の`~/.claude/settings.json`にのみマージされる。
  dotfiles固有の運用前提（`~/.claude/`がchezmoi配布先、個人の命名規約など）に依存するチェック向け。
  配置した場合は`share/claude_settings_json_managed.posix.json`および同`win32.json`の
  `matcher`に新しいツール名を追加する必要があるか確認する
- `agent-toolkit/`（プラグイン）: `.claude-plugin/marketplace.json`経由で他者にも配布される。
  汎用的な制約・自動化（一般的な文字化け検出、PowerShell互換性チェックなど）向け。
  配置した場合は「バージョン更新」節の手順に従う

agent-toolkit配下の編集時、dotfiles固有名の混入を`scripts/claude_hook_pretooluse.py`の専用チェックがブロックする。
ブロック対象の個人プロジェクト名固定リストは当該スクリプト内で定義し、OSS公開プロジェクト名はwarning通知に留める。
スキル名・pytoolsコマンド名・scripts名はhook実行時に当該ディレクトリをスキャンして動的取得する。
外部CLI参照は`_EXTERNAL_CLI_ALLOWED`登録識別子に限り`command -v`等の存在検査経由で許容し、
追加時は同スクリプトの同定義周辺コメントの基準に従う。

## 複数hook共存時の識別子

agent-toolkitのhookが利用者環境の他hookと同一イベントで共存する場合がある。
自身のhookメッセージを他hookから判別するため、`[auto-generated: agent-toolkit/<hook>]`形式のプレフィックスを行頭に置く。
プレフィックス・サフィックスの規約は`agent-toolkit/skills/agent-standards/references/claude-hooks.md`の
「コーディングエージェント宛てメッセージの標識」節に従う。

## marketplace管理

`update-dotfiles`（`chezmoi apply`後処理）は`pytools/_internal/install_claude_plugins.py`経由で
agent-toolkitプラグインを自動インストール・更新する。marketplace配布は次の経路で構成される。

- bootstrap経路: `install-claude.sh`/`install-claude.ps1`がGitHub型として登録する
- chezmoi apply経路: 後処理がdirectory型（dotfilesリポジトリの絶対パス直接参照）で維持し、
  GitHub型登録が残存する環境では自動でdirectory型へマイグレーションする
- ローカル編集の反映: `agent-toolkit/`配下の編集は`chezmoi apply`（または`update-dotfiles`）でデプロイし、
  Claude Code再起動か`/reload-plugins`で反映する。version bumpは不要

## コミットメッセージ方針と.gitmessage

`agent-toolkit:commit`スキルのコミットメッセージ方針と`.gitmessage`は配布範囲が異なるため意図的に重複させる。SSOT化しない。
