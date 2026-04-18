# agent-toolkit 利用ガイド

本リポジトリでは、Claude Codeの体験を向上させるスキルなどのセット「agent-toolkit」を提供している。

## コンセプト

1. 標準動作のカスタマイズ — デフォルトのClaude Codeの振る舞いを大規模開発に耐える品質レベルへ引き上げる。
   判断基準が曖昧な場面での事前相談の徹底、lint抑制時のユーザー確認の必須化、検証からコミットまでの流れの自動化など、具体的なふるまいをチューニングしている
2. 品質の治安維持 — コードスタイルや設計が崩れたプロジェクトではLLMも既存コードに引きずられ同レベルの質のコードを量産してしまう（割れ窓理論）。
   各言語のモダンなイディオム・禁止パターン・セキュリティ注意点・テスト方針を明示し、プロジェクトの初期状態によらず一定の品質ラインを維持する
3. 機能仕様の知識補完 — Claude Codeの機能は比較的新しく、LLMの訓練データに十分反映されていない可能性がある。
   rulesの`paths` frontmatter、skillsのprogressive disclosure、hookスクリプトの出力フィールドなど、明文化された仕様に基づいて作業できるようにする

Anthropic公式のsuperpowersスキルと重複する内容は多いが、日本語環境での確実なトリガーと大規模開発向けの細かな制御のために独自に作成している。
性質上、頻繁な改訂が発生する。

agent-toolkitルールファイルとプラグインの2つのコンポーネントで構成される。

- ルールファイル — `~/.claude/rules/agent-toolkit/`に配置されるルールファイル。自動読み込みされ、行動原則・運用方針・記述スタイルなどの共通指示を提供する
- プラグイン — Claude Codeのuser scopeにインストールするプラグイン。フック・スキルを提供し、場面に応じたオンデマンドの機能拡張を担う

両者は相互依存しており、基本的に同時に導入する前提である。

部分的に動作を変えたい場合は、
ユーザー側の`~/.claude/CLAUDE.md`・プロジェクトの`CLAUDE.md`・プロンプトでの指示などで上書きできる。
優先度はルールファイル側に明記している。

## クイックスタート

### 1. uvのインストール

プラグインは[uv](https://docs.astral.sh/uv/)に依存する。
事前にインストールしておく。

- Linux: `curl -fsSL https://astral.sh/uv/install.sh | sh`
- Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

### 2. ツールキットのインストール

先にClaude Codeをインストールしておくこと。
インストーラーは`claude` CLIの存在を前提としており、未検出時はエラー終了する。

ツールキットをインストールするには以下のワンライナーを実行する。

- Linux:

    ```bash
    curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.sh | bash
    ```

- Windows:

    ```cmd
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.ps1 | iex
    ```

ルールファイルが`~/.claude/rules/agent-toolkit/`へ配置され、
agent-toolkitプラグインがuser scopeへインストール・更新される。
再実行すると常に最新版へ同期される。

インストール後、非公式のPlugin Marketplaceはデフォルトで自動更新が無効のため、初回のみ手動で有効化する必要がある。

1. Claude Code内で`/plugin`を実行
2. `Marketplaces`タブで`ak110-dotfiles`を選択
3. `Enable auto-update`を選択

### 3. Claude Codeの推奨設定

以下の設定を適用しておくと、Claude Codeを快適に使える。

#### `~/.claude/settings.json`

- `autoMemoryEnabled`: `false`（自動メモリー機能を無効化）
- `showClearContextOnPlanAccept`: `true`（plan mode承認時にコンテキストクリアの選択肢を表示）
- `env.CLAUDE_CODE_NO_FLICKER`: `"1"`（画面のちらつきを抑制）
- `permissions`: 許可・拒否するツールやパターンを記述する（[例](https://github.com/ak110/dotfiles/blob/master/share/claude_settings_json_managed.json)）

#### `/config`コマンド

- `Verbose output`: 有効
- `Default permission mode`: `Plan mode`
- `Language`: `Japanese`

#### `/plugin`コマンド

claude-plugins-officialから以下を導入する。

- 推奨: `context7`・`typescript-lsp`
- 任意: `claude-md-management`・`skill-creator`
- 無効: `pyright-lsp`（Claude Codeにインストールを推奨されるが誤動作が多いため、インストール後に`Disable`することを推奨）

#### VSCode設定（お好みで）

ターミナル内での右クリックによる意図しない貼り付けを防ぎたい場合、`settings.json`へ以下を追加する。

```json
{
  "terminal.integrated.rightClickBehavior": "nothing"
}
```

### 4. codex MCPサーバーのセットアップ（推奨）

後述の`plan-mode`スキルはcodex MCPによる計画ファイルレビューを前提としている。
以下のコマンドで登録しておくと、計画ファイル作成時のレビューが自動で利用できる。

```bash
claude mcp add --scope=user codex codex mcp-server
```

dotfiles利用者は`update-dotfiles`/`chezmoi apply`の後処理で自動登録される（既登録時はスキップ）。
codex CLI自体のセットアップは別途行うこと。

## 構成と機能

### 常時有効な仕組み

以下のルールファイルとフックはセッション開始時から常に有効である。

ルールファイル（`~/.claude/rules/agent-toolkit/`配下）:

- `agent.md` — 基本原則・運用方針・検証とコミットの流れなど、全セッションで必要な共通指示（無条件ロード）
- `styles.md` — 構造と順序・言語と文体・日本語の表記ルール・コメントとドキュメント・コマンドラインオプションをまとめた記述スタイル指針（無条件ロード）

フック（agent-toolkitプラグイン）:

- 文字化け（U+FFFD）を含む`Write`/`Edit`/`MultiEdit`をブロック
- LF改行のみの`.ps1`/`.ps1.tmpl`への書き込みをブロック（Windows PowerShell 5.1対策）
- ロックファイルや`.venv/`/`node_modules/`など自動生成物の手編集をブロック
- シークレットらしき値の書き込みや、ホームディレクトリの絶対パスのハードコードを警告・ブロック
- テスト未実行のまま`git commit`を実行しようとした場合に警告する（PostToolUse連携）
- `git log`に`--decorate`がない場合に自動で挿入する
- `codex exec`（`resume`以外）の実行前に未決事項の確認を促す
- 未コミット変更がある場合、Stopのapprove時にgit statusをユーザーに表示する（LLMコンテキスト外）
- `~/.claude/plans/*.md`編集後に計画ファイルの構成（必須H2の欠損・順序違反・予期せぬH2・`変更履歴`の末尾以外配置）を検査して警告する
- ユーザーからの修正指示が多い場合やcodexレビュー不合格が多い場合に、CLAUDE.md更新を提案する

### オンデマンドのスキル

場面特化型の指示をオンデマンドで呼び出すスキルを提供する。
常時コンテキストを消費せず、該当する作業に着手したときだけロードされる。

- `coding-standards` — コード・テストコードの新規作成・修正・レビュー時に呼び出すコーディング品質とテスト方針のベース指示。
  言語別の詳細（Python/TypeScript/Rust/C#/PowerShell/Windowsバッチ）は`references/<言語>.md`にprogressive disclosureで分割。
  プロジェクト固有の`CLAUDE.md`や`.claude/rules/`が優先で、本スキルはそれを補完するベースライン
- `writing-standards` — ドキュメントの新規作成・修正・レビュー時に呼び出すドキュメント品質のベース指示。
  Markdown記述スタイル・README規約・技術文書の書き方を含む。`styles.md`の記述スタイルを共通基盤とし、ドキュメント固有の品質基準を追加する
- `plan-mode` — plan mode開始時・複雑な指示受領時・バグ障害イシュー調査相談時に呼び出す計画ファイル作成手順。
  計画ファイルの構成テンプレート、codexレビュー手順（MCP優先・CLIフォールバック）、変更履歴の書き方までを統合。
  曖昧な指示時には計画ファイル作成前に選択肢提示で要件を確定させる対話フェーズの指針も含む。
  バグ・障害・イシュー調査相談を受けた時は`references/bugfix.md`（対症療法で済ませず根本原因の追跡と再発防止まで行う4ステップ手順）も併せて読み込む
- `claude-meta-rules` — `CLAUDE.md`・`.claude/rules/`・`.claude/skills/`・hooks系ファイル編集時に呼び出すメタガイド。
  訓練データにないClaude Code独自機能の仕様補完と、コンテキスト汚染を避ける記述原則を集約
- `tidy-unpushed-commits` — 複数の未プッシュコミットを慎重で再現性のある手順で整理する（squash・reorder・メッセージ書き直し）。
  退避refとツリー差分検証で最終ツリーの同一性を機械的に担保する。
  直前コミットへのamendや特定コミットへのfixupで済む場合は`agent.md`の軽量パターンに自動分岐する
- `pyfltr-usage` — pyfltrの使い方・JSONL出力の解釈方法・サブコマンドの使い分けを参照できるリファレンス。
  日常的なpyfltr利用に必要な情報を自己完結的に含み、詳細な設定情報が必要な場合のみ`llms.txt`から個別ページを取得する構成
- `pytilpack-usage` — pytilpackのモジュール構成・代表的な使い方・APIドキュメント参照方法のリファレンス。
  `llms.txt`を段階的に取得して必要なモジュールのAPI情報を参照する構成
- `gitlab-ci-usage` — `.gitlab-ci.yml`編集時のキーワード仕様・典型パターン・lint手段・トラブルシューティング観点の参照リファレンス。
  キーワードの網羅的な仕様は公式ドキュメントをWebFetchして参照する導線のみを持ち、スキル本体は代表URL表と典型パターンのスニペットに絞ってコンテキストを節約する
- `spec-driven` — 軽量SDD（Spec-Driven Development）ワークフロー。
  起動コマンド: `/spec-driven`（自動補完で`/agent-toolkit:spec-driven`になる）
  - 想定ユースケース: 大規模コードベースへの機能追加・既存機能改修で、検討漏れ・デグレードを抑え、設計判断を恒常配置ドキュメントに蓄積したい場合
  - Setup&Explore→Plan&Implement→Cleanupの3ステップで進行する。ステップ終端にユーザー確認ゲートは置かず、判断が必要な場面は都度ユーザーに確認する
  - ステップ1の成果物はリサーチ結果ファイル群のみに絞る（骨子・`README.md`エントリは作らない）
  - ステップ2前半冒頭で`EnterPlanMode`を明示的に呼び出し、計画ファイル1本（大枠方針と実装詳細を集約）を作成してcodexレビューに合格するまで繰り返す
  - `ExitPlanMode`の直後に`spec-designer`を呼び出し、作業版`.md`骨子・`README.md`エントリを一括生成させる。メインはその後TaskCreate登録と`spec-implementer`順次起動に進む
  - ドキュメント規約（恒常配置側）: 恒常配置は機能単位で`docs/features/{機能名}.md`・`docs/topics/{トピック名}.md`に置く。昇格時に`spec-driven-promote`で開発中配置から恒常配置の該当機能ファイルへマージ統合する
  - ドキュメント規約（開発中配置側）: 開発中配置は作業テーマ単位で`docs/v{next}/{作業テーマ名}.md`に置く（例: `SSO追加.md`）。バージョン全体の概要と目次は`docs/v{next}/README.md`（昇格時に役目を終える）。既存改修時は作業版`.md`内の「改修前スナップショット」節に恒常配置の改修前該当節を貼り付け、Before/After検証材料とする（昇格時は破棄）
  - `{next}`は次期リリースのバージョン番号でステップ1で確定する（末尾の`.0`は省略。例: `3.0.0`→`v3`、`1.2.0`→`v1.2`）。ここに記した具体パスは既定値で、プロジェクトのドキュメントで別の配置が規定されている場合はそちらに従う
  - サブエージェント一時ファイルのサフィックス命名規則（配置は`docs/v{next}/.cache/`配下）
    - `{作業テーマ名}.research-{nn}.md`（`spec-researcher`。`{nn}`はゼロパディング2桁連番）
    - `{作業テーマ名}.review-spec.md`（`spec-reviewer`）
    - `{作業テーマ名}.review-quality.md`（`code-quality-reviewer`）
    - レビュー指摘ファイルは差し戻しループで上書きされる。一時ファイル群はいずれもCleanupステップで削除する
    - 一時ファイルは`.cache/`配下に置きgit管理から外す（`.gitignore`への`docs/v*/.cache/`追記は`spec-driven-init`スキルが担当する）。pyfltrなど品質ツール群の多くが`.cache`をデフォルト除外対象とするため、中間成果物が品質チェックを阻害しない
  - 調査は`spec-researcher`、設計ドキュメント立ち上げは`spec-designer`、実装は`spec-implementer`の各サブエージェントへ分業し、メインセッションのコンテキスト消費を抑える
  - レビューは`spec-reviewer`（仕様適合性・ドキュメント整合性・計画ファイルからの転記漏れ・粒度差対応の整合）と`code-quality-reviewer`（コード品質）を作業テーマ単位で直列起動する。全タスク完了・最終反映・format/lint/test合格のあとに実行する。両レビュアーは指摘を上記サフィックス付きファイルへ書き出し、戻り値は判定・件数に絞ることでメインのコンテキスト消費を抑える
  - 自動トリガー条件: 既にspec-drivenで開発している形跡（恒常配置の整備・`CLAUDE.md`記述）があり、かつ大規模な機能追加・改修の相談を受けた場合に限る。ユーザーが呼び忘れていると判断できるときは、ユーザーに起動可否を確認した上で自動起動する
- `export-for-resume` — 議論が二転三転して会話のまとまりが失われた時に、現時点の決定事項・未決事項・現在の進捗を整理したmarkdownを作業プロジェクトルート直下へ書き出すスキル。
  起動コマンド: `/export-for-resume`（自動補完で`/agent-toolkit:export-for-resume`になる）
  - 想定ユースケース: コンテキストが肥大化して応答品質や速度が低下してきた時、またはセッション終了直前に現状スナップショットだけ残して後日再開したい時
  - `disable-model-invocation: true`を指定しており、モデルによる自動起動は発生しない。ユーザーが明示的にスキルを呼んだ時のみ動作する
  - 出力ファイル（`resume-<YYYYMMDD-HHmmss>.md`）を添えて新しいClaude Codeセッションを立ち上げ直し、冒頭でそのファイルを読ませて作業を再開する運用を想定する

一部のスキルは`/<skill-name>`形式で明示的に呼び出せる。
現在は`/tidy-unpushed-commits`・`/pyfltr-usage`・`/pytilpack-usage`・`/gitlab-ci-usage`・`/spec-driven`・`/export-for-resume`を提供している。

### 明示呼び出し専用のスキル

以下のスキルは、ユーザーが明示的にコマンドを入力したとき、または`spec-driven`から明示的に呼び出されたときのみ動作する。
ユーザー相談への自動起動対象ではない。
存在と起動名を知らなければ使えないため、用途と合わせてここに記載する。

- `spec-driven-init` — 既存プロジェクトにspec-drivenを導入するための初回整備スキル。
  起動コマンド: `/spec-driven-init`（自動補完で`/agent-toolkit:spec-driven-init`になる）
  - 想定ユースケース: 既存のソースコード・ドキュメント・既存`docs/v*/`群から、`docs/features/`・`docs/topics/`配下の恒常配置の初版を起こす（これらのパスは既定値で、プロジェクト規約で上書き可能）
  - 引数無しで起動し、走査結果をもとにユーザーとの対話で機能・横断トピックの単位を確定する
  - 起動時にまず`spec-driven`スキルを読み込み、配置規約・テンプレートの共通仕様を参照する
- `spec-driven-promote` — 開発中バージョンディレクトリの作業版を恒常配置へマージ統合するスキル。
  起動コマンド: `/spec-driven-promote`（自動補完で`/agent-toolkit:spec-driven-promote`になる）
  - 想定ユースケース: リリース完了後のひと段落した時点で、`docs/v{next}/`配下のドキュメントを`docs/features/`・`docs/topics/`へ統合し、開発中バージョンディレクトリを削除する。上記のパスは既定値で、プロジェクト規約で上書き可能
  - 作業テーマと恒常配置の粒度差（1対多・多対1・1対1）を踏まえ、節別統合方針（追記・置換・併記の使い分け）でdiff提示→ユーザー合意→統合適用の半自動フローで進める
  - 引数無しで起動し、実装コード中の参照コメントのパスも恒常配置側へ一括書き換える
  - 起動時にまず`spec-driven`スキルを読み込み、配置規約・粒度差の前提・テンプレートの共通仕様を参照する

## 更新方法

ルールファイル・プラグインとも頻繁に更新されるため、定期的に最新化することを推奨する。

上記インストールコマンドを再実行することで更新できる。
