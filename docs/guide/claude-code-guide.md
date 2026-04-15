# Claude Code ルール / プラグインガイド

本リポジトリではClaude Code向けに以下の2種類の共有設定を提供している。

- ルール (`~/.claude/rules/agent-basics/` 配下) — 全プロジェクトで自動読み込みされるコーディング規約・運用方針
- プラグイン — Plugin Marketplace `ak110-dotfiles` 経由で配布するClaude Codeプラグイン

dotfiles管理側の情報（配布方式・配布元など）は [docs/guide/claude-code.md](claude-code.md) を参照。
ここでは配布されるルール・プラグインのインストール方法と内容を説明する。

## 前提条件

プラグインは [uv](https://docs.astral.sh/uv/) に依存する（hookスクリプトを `uv run --script` で実行するため）。
事前にインストールしておく必要がある。

- インストールコマンド例:
  - Linux: `curl -fsSL https://astral.sh/uv/install.sh | sh`
  - Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

## インストール

ルールとプラグインは同じワンライナーでまとめて導入できる。
ワンライナーはルールファイルを `~/.claude/rules/agent-basics/` へ配置したうえで、`claude` CLIを検出した場合は `agent-toolkit` プラグインも自動でuser scopeへインストールする。
`claude` CLI未導入の環境では案内メッセージのみ表示されるため、後述の手動インストールで追加する。

### Linux

```bash
curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.sh | bash
```

### Windows

```cmd
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.ps1 | iex"
```

### 手動インストール（プラグインのみ）

ワンライナー実行時に `claude` CLIが未検出だった場合や、Marketplace経由でプラグインだけ追加したい場合は以下を実行する。

```bash
claude plugin marketplace add ak110/dotfiles
claude plugin install agent-toolkit@ak110-dotfiles --scope user
```

project scopeで導入したい場合は、プロジェクトの `.claude/settings.json` に `enabledPlugins` と `extraKnownMarketplaces` を設定する。
開発者がフォルダーをtrustした時にClaude Codeがインストールを自動で提案する。

```json
{
  "extraKnownMarketplaces": {
    "ak110-dotfiles": {
      "source": {
        "source": "github",
        "repo": "ak110/dotfiles"
      }
    }
  },
  "enabledPlugins": {
    "agent-toolkit@ak110-dotfiles": true
  }
}
```

### 自動更新の有効化

非公式のPlugin Marketplaceはデフォルトで自動更新が無効のため、初回のみ手動で有効化する。

1. Claude Code内で `/plugin` を実行
2. `Marketplaces` タブで `ak110-dotfiles` を選択
3. `Enable auto-update` を選択

### codex MCPサーバーのセットアップ（推奨）

`plan-mode` スキルはcodex MCPによる計画ファイルレビューを前提としている。
以下のコマンドで登録しておくと、計画ファイル作成時のレビューが自動で利用できる。

```bash
claude mcp add --scope=user codex codex mcp-server
```

dotfiles利用者は `update-dotfiles` / `chezmoi apply` の後処理で自動登録される（既登録時はスキップ）。
codex CLI自体のセットアップは別途行うこと。

### 更新

ルール・プラグインとも頻繁に更新されるため、定期的に最新化することを推奨する。

- ルール: 上記インストールコマンドを再実行する。既存ファイルのfrontmatterを維持したままbodyのみ更新される
- プラグイン: 自動更新を有効化していればClaude Codeが自動で更新する

## コンセプト

本リポジトリのClaude Code共有設定は、ルールとプラグインを組み合わせて以下の狙いを実現している。

1. Claude Codeの動作カスタマイズ
    - デフォルトのClaude Codeの振る舞いをエンタープライズ開発に耐える品質レベルへ引き上げる
    - 具体的にチューニングしている主なふるまい:
        - 検証→コミットの流れを自動化（「コミットしますか」等の冗長確認を省略、未プッシュの類似変更はamend/fixupも許容）
        - 実装の複雑さが要求に不釣り合いなとき・判断基準が曖昧なときは必ず事前相談
        - lint抑制やインライン無視コメントはユーザー確認を必須化
        - 方針のドキュメント化を促す
    - 上記のふるまいは、必要に応じてユーザー側の~/.claude/CLAUDE.mdやプロジェクトのCLAUDE.md、プロンプトでの指示などで上書き可能（優先度として明記している）
2. 品質面の治安維持（割れ窓理論的発想）
    - コードスタイルや設計が崩れたプロジェクトではLLMも既存コードに引きずられ、同レベルの質のコードを量産してしまう（割れ窓理論）
    - ルール側のMarkdown記述スタイルと、`agent-toolkit`プラグインの`coding-standards`スキルで、各言語のモダンなイディオム・禁止パターン・セキュリティ注意点・テスト方針を明示している。
      プロジェクトの初期状態の良し悪しによらず一定の品質ラインを維持することでバグの発生を抑制する
    - この発想は共通ルール（`agent.md`）の「基本原則」「記述スタイル」節にも含まれ、ルールとスキルが分担して同じコンセプトを実現している
    - 記述スタイルの「トップダウン（段階的詳細化）」は、LLMが長文出力中に細部へ引きずられて全体構造や上位要件を見落としやすい性質を踏まえた対策。
      先に型定義・上位関数・見出し構造を記述してから詳細を追記することで見落としを防ぐ狙い
    - あくまで「ベース指示」であり、プロジェクト固有の規約は各`CLAUDE.md`やプロジェクト内`.claude/rules/`で上書きする前提
3. Claude Code自身の機能仕様の知識補完
    - Claude Codeの機能は比較的新しく、LLMの訓練データに十分反映されていない可能性がある
    - 対象例: rulesの`paths` frontmatter、skillsのprogressive disclosure、`CLAUDE.md`との使い分け、hookスクリプトの出力フィールドなど
    - `agent-toolkit`プラグインの`claude-meta-rules`スキルが`.claude/`配下の設定ファイル編集時に呼び出され、
      訓練データ頼みの推測ではなく明文化された仕様に基づいて作業できる
    - 同じ発想で、今後Claude Codeに新機能が追加された場合も、
      該当機能の編集時だけ呼び出されるスキルを追加する余地がある

## ルール

### ファイル構成

`~/.claude/rules/agent-basics/` 配下に以下のファイルが配置される。

- `agent.md`: 基本原則・運用方針・記述スタイル・検証/コミットの流れなど、全セッションで必要な共通指示（無条件ロード）
- `markdown.md`: Markdown記述スタイル（`.md` / `.mdx` 編集時のみロード）

言語別コーディング規約・テスト方針、計画モード、バグ対応、Claude Code設定ファイル記述ガイドなど場面特化型の指示は `agent-toolkit` プラグインのスキルが担う。
必要な場面でClaudeが自動呼び出しする構成のため、常時コンテキスト消費を抑えられる。

`CLAUDE.md` はプロジェクト固有の情報を記述するファイルとして、配布の管理対象外。
プロジェクトごとに手動で管理する (`/init` コマンドなどを活用するのも手)。

### カスタマイズ

インストール後、`~/.claude/rules/agent-basics/` 配下のファイルは必要に応じて編集できる。
たとえば `paths` frontmatterを変更すれば、各ルールの適用範囲を限定できる。
配布元の `agent.md` のようにfrontmatterを持たないファイルでも、ローカルでfrontmatterを追記していれば再実行時に維持される。

frontmatter以外（body部分）を編集すると、再実行時に上書きされて変更が破棄されてしまう。
bodyをカスタマイズしたい場合は、`paths` frontmatterに存在しない拡張子を指定して該当ルールを実質無効化する。
たとえば `paths: ["**/*.__disabled__"]` のように設定したうえで、別ファイルとして独自ルールを管理するのを推奨する。

### バックアップ

bodyに差分があった場合、旧ファイルは `~/.claude/rules-backup/agent-basics-<timestamp>/` に退避される。
バックアップ先を `~/.claude/rules/` の外に置いているのは、退避先が同じツリー内にあると古いルールも再帰的に読まれてしまうため。
不要になったバックアップは適宜削除する。

## プラグイン

ルールだけではカバーしきれない領域を補うためのプラグインを提供する。
本リポジトリ自体をClaude CodeのPlugin Marketplace (`ak110-dotfiles`) として登録できるようにしてあり、今後もプラグインを追加する可能性がある。

### agent-toolkit

Claude Code全体を補強するツールキット型のプラグイン。
hook・スキル・スラッシュコマンドを一体で配布し、ルール（`agent.md`）と組み合わせて前述のコンセプトを実現する。

#### hook

好ましくない編集やBash呼び出しを`PreToolUse`段階で検出・制御し、コードベースの破壊や訓練データ由来の誤った思い込みによる事故を未然に防ぐ。
`PostToolUse`・`Stop`とも連携し、検証→コミットの運用や作業振り返りを補助する。

主なチェック内容は以下。

- 文字化け (U+FFFD) を含む`Write` / `Edit` / `MultiEdit`をブロック
- LF改行のみの`.ps1` / `.ps1.tmpl`への書き込みをブロック（Windows PowerShell 5.1対策）
- ロックファイルや`.venv/` / `node_modules/`など自動生成物の手編集をブロック
- シークレットらしき値の書き込みや、ホームディレクトリの絶対パスのハードコードを警告・ブロック
- テスト未実行のまま`git commit`を実行しようとした場合に警告する（Bash、PostToolUse連携）
- `git log`に`--decorate`がない場合に自動で挿入する（Bash）
- `codex exec`（`resume`以外）の実行前に未決事項の確認を促す（Bash）
- 未コミット変更がある場合、Stopのapprove時にgit statusをユーザーに表示する（Stop、LLMコンテキスト外）
- ユーザーからの修正指示が多い場合やcodexレビュー不合格が多い場合に、CLAUDE.md更新を提案する（Stop）

#### スキル

場面特化型の指示をオンデマンドで呼び出す。常時コンテキストを消費せず、該当する作業に着手したときだけロードされる。

- `coding-standards`: コード・テストコードの新規作成・修正・レビュー時に呼び出すコーディング品質とテスト方針のベース指示。
  言語別の詳細（Python/TypeScript/Rust/C#/PowerShell/Windowsバッチ）は`references/<言語>.md`にprogressive disclosureで分割。
  プロジェクト固有のCLAUDE.mdや`.claude/rules/`が優先で、本スキルはそれを補完するベースライン
- `plan-mode`: plan mode開始時・複雑な指示受領時に呼び出す計画ファイル作成手順。
  計画ファイルの構成テンプレート、codexレビュー手順（MCP優先・CLIフォールバック）、変更履歴の書き方までを統合
- `bugfix`: バグ・障害・イシュー調査対応の4ステップ標準手順（根本原因特定・対策決定・類似箇所見直し・再発防止）
- `claude-meta-rules`: CLAUDE.md・`.claude/rules/`・`.claude/skills/`・hooks系ファイル編集時に呼び出すメタガイド。
  訓練データに無いClaude Code独自機能の仕様補完と、コンテキスト汚染を避ける記述原則を集約
- `tidy-unpushed-commits`: 複数の未プッシュコミットを慎重で再現性のある手順で整理する（squash・reorder・メッセージ書き直し）。
  退避refとツリー差分検証で最終ツリーの同一性を機械的に担保し、乱暴な`git reset`は使わない。
  直前コミットへのamendや特定コミットへのfixupで済む場合はagent.mdの軽量パターンに自動分岐する
- `pyfltr-usage`: pyfltrの使い方・JSONL出力の解釈方法・サブコマンドの使い分けを参照できるリファレンス。
  日常的なpyfltr利用に必要な情報を自己完結的に含み、詳細な設定情報が必要な場合のみllms.txtから個別ページを取得する構成
- `pytilpack-usage`: pytilpackのモジュール構成・代表的な使い方・APIドキュメント参照方法のリファレンス。
  llms.txtを段階的に取得して必要なモジュールのAPI情報を参照する構成

#### スラッシュコマンド

一部のスキルは`/<skill-name>`形式で明示的に呼び出せる。
現在は`/tidy-unpushed-commits`・`/pyfltr-usage`・`/pytilpack-usage`を提供している。

## 移行: edit-guardrails → agent-toolkit

旧プラグイン `edit-guardrails` は `agent-toolkit` に改名・統合された。
`edit-guardrails` がインストール済みの場合は以下のコマンドで削除する。

```bash
claude plugin uninstall edit-guardrails@ak110-dotfiles
```

dotfiles利用者は `update-dotfiles` を実行すれば自動的に削除される。
