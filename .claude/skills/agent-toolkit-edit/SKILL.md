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

- `agent-toolkit/`配下: プラグイン
- `agent-toolkit/rules/`配下: ルールファイル（`01-agent.md`が基本原則・運用方針・言語表現を単独で担う）
- `~/.claude/rules/agent-toolkit/`: ルールファイルの配布先（直接編集不可）

参照方向はdotfilesリポジトリ→プラグイン、およびプラグイン↔ルールファイルを許容する。
配置先は「いつコンテキストへ読み込ませたいか」で判断する。

- 常時自動ロードしたい一般指針はルールファイルへ置く
- 特定タスクでのみ必要な指針はスキル本体に残す

## 配布物としての記述方針

配布先の利用者は本リポジトリのdotfiles利用者とは限らないため、手元プロジェクト固有の前提を断定的に書かない。

- 自己言及的な表現・特定設定値の前提・特定ディレクトリ構成の前提を断定せず、
  異なり得る条件は条件付き表現（「`～`設定が有効な場合、」など）で書く
- 仕様参照としてのルール名・設定キー名・選択肢の説明は記述してよい
- 配布物のdocstring・コメント・本文には配布物自身の挙動・仕様のみを記述し、
  利用者環境側の連携設計（個人フックとの優先順序など）は書かない
- 配布物の出力文字列・フックメッセージ・docstringにリポジトリ管理外の個人メモファイル名を含めない
- 配布物内の記述が参照するSSOTは配布物内に配置し、dotfiles固有ファイル・非配布対象ファイルを参照先にしない
- 配布物文面は計画段階（plan modeの提案文・改訂案・例示文など）にも本節の方針を事前適用し、
  `## 変更内容`のdiff改訂後文面の確定前に+側文字列の固有名照合を実施して一般化表現へ置き換える
  （照合対象は`scripts/claude_hook_pretooluse.py`の固有名ブロック対象）
- 配布物スキル本文でhook内部の実装挙動
  （ハッシュ照合・SHA256記録・ブロック機構・状態フラグ書き込み等）を説明する記述を書かない。
  利用者には挙動の観測結果（特定操作がブロックされる・警告が返る等）のみを提示する。
  - 例外: SSOT目的で状態フラグ一覧・hook間連携仕様を集約する節
    （`agent-toolkit:agent-standards`「セッション状態フラグ」節等）は本規定の対象外とする

スキル・サブエージェント編集時は次を守る。

- SKILL.md本体に必要な情報は本体に直接書き、`references/`から別の`references/`を多段参照させない。
  スキル・サブエージェント間で記述が重複しても読み込まれるコンテキストが異なるため無理に共通化しない
  - 例外: 相互参照が発生している共通観点は横断スキル配下`references/`へSSOT化して参照形式へ縮減してよい
- 並行する手順を別スキルに新設する際は、既存スキルの表記との整合を確認する
- 「実行時エラーで判明する仕様」「具体例」は再発リスクと影響度を踏まえて保持判断する
- agent-toolkit同梱スキル参照は`agent-toolkit:<skill-name>`形式に統一する。
  サブエージェント名の表記規約は`agent-toolkit/rules/03-claude-code.md`「forkスキルとagents定義」節に従う
  （起動指示は完全修飾形、地の文呼称は短縮形を許容する）。
  プロジェクトローカルスキル（`.claude/skills/`配下）はプラグイン接頭辞を付けず素のスキル名で参照する
- 規範文言中でscope-escalation検出対象パターン（工程スキップ・作業省略・部分対応・規範違反承知の続行等）に
  言及する場合は肯定形で書き、否定形の生表記を本文へ含めない（例: 「登録された全工程を実施する」）
  - 否定形の生表記は`process-omission`カテゴリでヒットしEdit/Writeがブロックされる
  - 検出対象パターン一覧の典拠は`agent-toolkit/skills/agent-standards/references/scope-escalation-phrases.md`

### プラグイン内リソースの参照書式

利用者環境で実行される実行時パス（`hooks.json`の`command`・エージェント/スキル本文の実行コマンド例）は`${CLAUDE_PLUGIN_ROOT}/<相対パス>`形式に統一する。
プラグイン配布物のルートはインストール先で動的に解決されるため、dotfilesリポジトリ相対パスは利用者環境で実行不能となる。
規範文書内で役割を説明する言及（「〜は`agent-toolkit/scripts/<name>.py`が担う」等）はリポジトリ相対表記のままでよい。
判定基準は当該パスを利用者環境で実行するか否かとする。

## スキル間の連携

`agent-toolkit:plan-mode`から作業を開始する。作成した計画ファイルは`ExitPlanMode`を合意ゲートとして通過し、
Agentツールで`agent-toolkit:plan-impl-executor`を起動して引き継ぐ。
計画ファイルの`## 実行方法`のレビューステップに「レビューは実施しない」とあればレビュー工程をスキップし、
それ以外は記載のスキル・エージェント（既定は`agent-toolkit:careful-review`スキル）へ引き継ぐ。
レビューは`plan-spec-reviewer`と`plan-impl-reviewer`を全体差分対象に並列起動する。
指摘があればメインが統合し、修正再実装・コミット統合・再レビューを指摘解消まで繰り返す。

## バージョン更新

本節のバージョン更新規定は`agent-toolkit/`配下（agent-toolkitプラグイン配布物）のみを対象とする。
詳細手順は`references/version-bump.md`に集約する。
`version`／`description`は以下の箇所で完全に同一文字列に保つ。

- `agent-toolkit/.claude-plugin/plugin.json`
- `.claude-plugin/marketplace.json`の`plugins[]`内`name == "agent-toolkit"`のエントリ
整合性は`agent-toolkit/scripts/pretooluse_test.py`の`TestManifestSsot`が検査し、`uvx pyfltr run`で自動的に失敗する。
Codex向けmanifestはこの2ファイルを正本として`scripts/sync_codex_plugin_manifests.py`が生成する。
生成物を手動編集してはならない。

## 同期先ドキュメント

- `docs/guide/claude-code-guide.md`「agent-toolkit」セクションのチェック内容要約は、要約が変わる変更時に更新する。
  対象は新しいcheck追加・既存check削除・検出範囲の大きな変更・依存ツールの変更・新規プラグイン追加を含む
- `install-claude.sh`の`FILES`・`install-claude.ps1`の`$files`・
  `agent-toolkit/rules/`配下のmdファイル一覧は完全一致を保つ
  （整合性は`install_script_ssot_test.py`検査、自動同期手段は持たない）
- 配布物スキル本体の外部インターフェース（判定区分・出力フォーマット・後始末コマンド分岐・サマリー表現など）へ
  新規追加・削除・改名を加える場合は連携整合を保つ。
  既知の呼び出し元スキル群を`grep -rn`で洗い出し、連携先の対応記述を同一計画内で同時更新する

## セッション状態フラグ

`agent-toolkit`プラグインが定義する全フラグ一覧のSSOTは`agent-toolkit:agent-standards`スキル本体（SKILL.md）
「セッション状態フラグ」節に置く。フラグを追加・変更する際は当該節を更新する。

## 編集手順

push前にbumpが必須（同じバージョンでは`claude plugin update`が「最新です」と返し利用者へ配信されないため）。

1. 「バージョン更新」の判定基準に該当する場合は`scripts/agent_toolkit_bump.py {patch|minor|major}`を実行する
2. `description`を変更する場合はSSOTの2ファイルを手で同期する
3. `scripts/sync_codex_plugin_manifests.py`を実行してCodex向け派生manifestを同期する
4. 必要なら`docs/guide/claude-code-guide.md`のチェック内容リストを更新する
5. `uvx pyfltr run-for-agent`を実行し、SSOTテストを含む全テストがgreenであることを確認する
6. 変更をコミットする

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
個人プロジェクト名固定リストは当該スクリプト内で定義し、OSS公開プロジェクト名はwarning通知に留める。
スキル名・pytoolsコマンド名・scripts名はhook実行時にディレクトリをスキャンして動的取得する。
外部CLI参照は`_EXTERNAL_CLI_ALLOWED`登録識別子に限り`command -v`等の存在検査経由で許容する。

## 複数hook共存時の識別子

agent-toolkitのhookが利用者環境の他hookと同一イベントで共存する場合がある。
自身のhookメッセージを他hookから判別するため、`[auto-generated: agent-toolkit/<hook>]`形式のプレフィックスを行頭に置く。
プレフィックス・サフィックスの規約は`agent-toolkit/skills/agent-standards/references/claude-hooks.md`の
「コーディングエージェント宛てメッセージの標識」節に従う。

## marketplace管理

`update-dotfiles`（`chezmoi apply`後処理）はClaude Code向けagent-toolkitプラグインを自動インストール・更新する。
処理は`pytools/_internal/install_claude_plugins.py`が担う。marketplace配布経路は次のとおり。

- bootstrap: `install-claude.sh`/`install-claude.ps1`がGitHub型として登録する
- chezmoi apply: 後処理がdirectory型（絶対パス直接参照）で維持し、GitHub型登録残存時は自動でマイグレーションする
- ローカル編集の反映: `chezmoi apply`（または`update-dotfiles`）でデプロイし、
  Claude Code再起動か`/reload-plugins`で反映する（version bumpは不要）

Codex向け`.codex-plugin/plugin.json`と`.agents/plugins/marketplace.json`はClaude Code向けmanifestを
正本として専用同期スクリプトで生成する。pre-commitは書き込みモードで毎回再生成する。
Codex生成物を手動編集せず、正本の変更後に同期スクリプトを実行する。
Codex hookはイベント名、matcher、入力契約を確認した許可表の定義だけを生成する。
`chezmoi apply`後処理はCodex marketplaceを登録し、agent-toolkit pluginを導入・更新する。

## コミットメッセージ方針と.gitmessage

`agent-toolkit:commit`スキルのコミットメッセージ方針と`.gitmessage`は配布範囲が異なるため意図的に重複させる。SSOT化しない。
