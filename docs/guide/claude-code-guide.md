# Claude Code拡張ツールキット 利用ガイド

本リポジトリでは、Claude Codeの体験を向上させる拡張ツールキットを提供している。

dotfiles管理側の情報（配布方式・配布元など）は[docs/guide/claude-code.md](claude-code.md)を参照。
ここではコンセプト・導入方法・機能を説明する。

## コンセプト

本ツールキットの狙いは以下の3つ。

1. 標準動作のカスタマイズ — デフォルトのClaude Codeの振る舞いを大規模開発に耐える品質レベルへ引き上げる。
   判断基準が曖昧な場面での事前相談の徹底、lint抑制時のユーザー確認の必須化、検証からコミットまでの流れの自動化など、具体的なふるまいをチューニングしている
2. 品質の治安維持 — コードスタイルや設計が崩れたプロジェクトではLLMも既存コードに引きずられ同レベルの質のコードを量産してしまう（割れ窓理論）。
   各言語のモダンなイディオム・禁止パターン・セキュリティ注意点・テスト方針を明示し、プロジェクトの初期状態によらず一定の品質ラインを維持する
3. 機能仕様の知識補完 — Claude Codeの機能は比較的新しく、LLMの訓練データに十分反映されていない可能性がある。
   rulesの`paths` frontmatter、skillsのprogressive disclosure、hookスクリプトの出力フィールドなど、明文化された仕様に基づいて作業できるようにする

Anthropic公式のsuperpowersスキルと重複する内容は多いが、日本語環境での確実なトリガーと大規模開発での細かな制御のために独自に作成している。
性質上、頻繁な改訂が発生する。

ツールキットはagent-basics（ルールファイル）とagent-toolkit（プラグイン）の2つのコンポーネントで構成される。

- agent-basics — `~/.claude/rules/agent-basics/`に配置されるルールファイル。自動読み込みされ、行動原則・運用方針・記述スタイルなどの共通指示を提供する
- agent-toolkit — Claude Codeのuser scopeにインストールするプラグイン。フック・スキルを提供し、場面に応じたオンデマンドの機能拡張を担う

両者は相互依存しており、基本的に同時に導入する前提である。

様々な機能を持つため、部分的に無効化したい場合などはユーザー側の`~/.claude/CLAUDE.md`やプロジェクトの`CLAUDE.md`、プロンプトでの指示などで上書きできる設計としている（優先度としてルールファイル側に明記している）。

## クイックスタート

### 1. uvのインストール

プラグインは[uv](https://docs.astral.sh/uv/)に依存する。
事前にインストールしておく。

- Linux: `curl -fsSL https://astral.sh/uv/install.sh | sh`
- Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`

### 2. ツールキットのインストール

ツールキットをインストールするには以下のワンライナーを実行する。

- Linux:

    ```bash
    curl -fsSL https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.sh | bash
    ```

- Windows:

    ```cmd
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.ps1 | iex
    ```

ルールファイルが`~/.claude/rules/agent-basics/`へ配置され、
`claude` CLIを検出した場合はagent-toolkitプラグインもuser scopeへ自動インストールされる。

`claude` CLI未導入の環境では案内メッセージのみ表示される。

インストール後、非公式のPlugin Marketplaceはデフォルトで自動更新が無効のため、初回のみ手動で有効化する必要がある。

1. Claude Code内で`/plugin`を実行
2. `Marketplaces`タブで`ak110-dotfiles`を選択
3. `Enable auto-update`を選択

### 3. Claude Codeのおすすめ設定

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
- 無効推奨: `pyright-lsp`（Claude Codeにインストールを推奨されるが誤動作が多いため、インストール後に`Disable`することを推奨）

#### VSCode設定（お好みで）

ターミナル内での右クリックによる意図しない貼り付けを防ぎたい場合、`settings.json`へ以下を追加する。

```json
{
  "terminal.integrated.rightClickBehavior": "nothing"
}
```

## 構成と機能

### 常時有効な仕組み

以下のルールファイルとフックはセッション開始時から常に有効である。

ルールファイル（`~/.claude/rules/agent-basics/`配下）:

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
- ユーザーからの修正指示が多い場合やcodexレビュー不合格が多い場合に、CLAUDE.md更新を提案する

### オンデマンドのスキル

場面特化型の指示をオンデマンドで呼び出すスキルを提供する。
常時コンテキストを消費せず、該当する作業に着手したときだけロードされる。

- `coding-standards` — コード・テストコードの新規作成・修正・レビュー時に呼び出すコーディング品質とテスト方針のベース指示。
  言語別の詳細（Python/TypeScript/Rust/C#/PowerShell/Windowsバッチ）は`references/<言語>.md`にprogressive disclosureで分割。
  プロジェクト固有の`CLAUDE.md`や`.claude/rules/`が優先で、本スキルはそれを補完するベースライン
- `writing-standards` — ドキュメントの新規作成・修正・レビュー時に呼び出すドキュメント品質のベース指示。
  Markdown記述スタイル・README規約・技術文書の書き方を含む。`styles.md`の記述スタイルを共通基盤とし、ドキュメント固有の品質基準を追加する
- `plan-mode` — plan mode開始時・複雑な指示受領時に呼び出す計画ファイル作成手順。
  計画ファイルの構成テンプレート、codexレビュー手順（MCP優先・CLIフォールバック）、変更履歴の書き方までを統合
- `bugfix` — バグ・障害・イシュー調査対応の手順。対症療法で済ませず根本原因の追跡と再発防止に踏み込む
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

一部のスキルは`/<skill-name>`形式で明示的に呼び出せる。
現在は`/tidy-unpushed-commits`・`/pyfltr-usage`・`/pytilpack-usage`・`/gitlab-ci-usage`を提供している。

## 補足

### codex MCPサーバーのセットアップ（推奨）

`plan-mode`スキルはcodex MCPによる計画ファイルレビューを前提としている。
以下のコマンドで登録しておくと、計画ファイル作成時のレビューが自動で利用できる。

```bash
claude mcp add --scope=user codex codex mcp-server
```

dotfiles利用者は`update-dotfiles`/`chezmoi apply`の後処理で自動登録される（既登録時はスキップ）。
codex CLI自体のセットアップは別途行うこと。

### 更新方法

ルールファイル・プラグインとも頻繁に更新されるため、定期的に最新化することを推奨する。

- ルールファイル: 上記インストールコマンドを再実行する
- プラグイン: 自動更新を有効化していればClaude Codeが自動で更新する

### プラグインの手動インストール

ワンライナー実行時に`claude` CLIが未検出だった場合や、Marketplace経由でプラグインだけ追加したい場合は以下を実行する。

```bash
claude plugin marketplace add ak110/dotfiles
claude plugin install agent-toolkit@ak110-dotfiles --scope user
```

project scopeで導入したい場合は、プロジェクトの`.claude/settings.json`に`enabledPlugins`と`extraKnownMarketplaces`を設定する。
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

`update-dotfiles`を併用する環境では、github型登録がそのまま維持される。
過去の`update-dotfiles`が残したdirectory型などの破損エントリがある場合も、自動でgithub型へ修復する。
対象は`known_marketplaces.json`と`settings.json.extraKnownMarketplaces`の両方である。

### 公式プラグインの自動管理

`update-dotfiles`実行時に、公式marketplace（`claude-plugins-official`）のプラグインの有効/無効を`pytools/_install_claude_plugins.py`が自動で揃える。

- 自動で無効化するプラグイン（`_AUTO_DISABLED_PLUGIN_IDS`定数で管理）: `claude plugin disable --scope user`を呼ぶ。アンインストールではなく`disabled`状態のまま残すため、再インストールされても次回の`update-dotfiles`で再度無効化される
- 自動で有効化するプラグイン（`_AUTO_ENABLED_PLUGIN_IDS`定数で管理、例: `context7`）: user scopeで未インストールなら`claude plugin install --scope user`で導入する。さらに`enabledPlugins`で明示的に`false`のときだけ`claude plugin enable --scope user`で再有効化する

対象を個別に外したい場合は定数を書き換える。一時的に`claude plugin enable <id>`で有効化しても、次回の`update-dotfiles`で再び無効化される点に注意する。

### edit-guardrailsからの移行

旧プラグイン`edit-guardrails`は`agent-toolkit`に改名・統合された。
`edit-guardrails`がインストール済みの場合は以下のコマンドで削除する。

```bash
claude plugin uninstall edit-guardrails@ak110-dotfiles
```

dotfiles利用者は`update-dotfiles`を実行すれば自動的に削除される。
