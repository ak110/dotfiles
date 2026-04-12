# Claude Code ルール / プラグイン

本リポジトリではClaude Code向けに以下の2種類の共有設定を提供している。

- ルール (`~/.claude/rules/agent-basics/` 配下) — 全プロジェクトで自動読み込みされるコーディング規約・運用方針
- プラグイン — Plugin Marketplace `ak110-dotfiles` 経由で配布するClaude Codeプラグイン

インストール手順は [docs/guide/claude-code.md](claude-code.md) を参照。
ここでは内容とカスタマイズ方法を説明する。

## ルール

### コンセプト

本ルール群は以下の狙いを持って構成されている。

1. Claude Codeの動作カスタマイズ
    - `agent.md` はデフォルトのClaude Codeの振る舞いを、エンタープライズ開発に耐える品質レベルへ引き上げるためのベース指示
    - 前提ユーザー像は「gitの履歴操作を必要に応じて自分で行える程度の習熟度を持つ開発者」。
      Claude Codeが積極的にコミット・amendを行っても、問題があればユーザー側で取り消せることを前提にしている
    - 具体的にチューニングしている主な振る舞い:
        - 検証→コミットの流れを自動化（「コミットしますか」等の冗長確認を省略、未プッシュの類似変更はamend/fixupも許容）
        - バグ対応3ステップ（根本原因特定 / 類似箇所の見直し / 再発防止）の徹底
        - 実装の複雑さが要求に不釣り合いなとき・判断基準が曖昧なときは必ず事前相談
        - lint抑制やインライン無視コメントはユーザー確認を必須化
2. 品質面の治安維持（割れ窓理論的発想）
    - コードスタイルや設計が崩れたプロジェクトではLLMも既存コードに引きずられ、同レベルの質のコードを量産してしまう（割れ窓理論）
    - 言語別ルール (`python.md` / `typescript.md` / `rust.md` / `csharp.md` および対応する `-test.md`) と `markdown.md` を用意している。
      これらは各言語のモダンなイディオム・禁止パターン・セキュリティ注意点・テスト方針を明示し、
      プロジェクトの初期状態の良し悪しによらず一定の品質ラインを維持することでバグの発生を抑制する
    - この発想は言語別ルールだけでなく `agent.md` の「基本原則」「コーディング品質」「記述スタイル」節にも含まれ、両者が分担して同じコンセプトを実現している
    - 記述スタイルの「トップダウン（段階的詳細化）」は、LLMが長文出力中に細部へ引きずられて全体構造や上位要件を見落としやすい性質を踏まえた対策。
      先に型定義・上位関数・見出し構造を記述してから詳細を追記することで見落としを防ぐ狙い
    - あくまで「ベース指示」であり、プロジェクト固有の規約は各 `CLAUDE.md` やプロジェクト内 `.claude/rules/` で上書きする前提
3. Claude Code自身の機能仕様の知識補完 (`claude.md` / `claude-rules.md` / `claude-skills.md`)
    - Claude Codeの機能は比較的新しく、LLMの訓練データに十分反映されていない可能性がある
    - 対象例: rulesの `paths` frontmatter、skillsのprogressive disclosure、`CLAUDE.md` との使い分けなど
    - `.claude/` 配下の設定ファイル編集時にメタルールが自動ロードされる
    - これらには各機能の正しい書き方・設計方針がまとめられている
    - これによりClaude Codeが自分の設定ファイルを編集する際に、
      訓練データ頼みの推測ではなく明文化された仕様に基づいて作業できる
    - 同じ発想で、今後Claude Codeに新機能が追加された場合も、
      該当機能の編集時だけロードされるメタルールを追加する余地がある

### ファイル構成

`~/.claude/rules/agent-basics/` 配下に以下のファイルが配置される。

- `agent.md`: 自動化すべき部分とユーザー確認すべき部分のバランス調整、コード品質の維持のためのルール（無条件ロード）
- `{言語}.md`: 言語固有のコーディング規約（`paths` frontmatterで該当言語ファイル編集時のみロード）
- `{言語}-test.md`: 言語固有のテスト方針（同上）
- `markdown.md`: Markdown記述スタイル（`.md` / `.mdx` 編集時のみロード）
- `claude.md`: CLAUDE.md・ルール・スキルの記述ガイドライン（`.claude/` 配下やCLAUDE.md編集時のみロード）
- `claude-rules.md`: Claude Codeのrules機能仕様を参照するメタルール（`.claude/rules/` 編集時のみロード）
- `claude-skills.md`: Claude Codeのskills機能仕様を参照するメタルール（`.claude/skills/` 編集時のみロード）

`agent.md` 以外は `paths` frontmatterで該当拡張子のファイルを読んだときのみロードされる。
セッション開始時のコンテキスト消費を抑えるための仕組みであり、プロジェクト単位の厳密な分離ではない (たとえばPythonファイルを編集すれば `python.md` はロードされる)。

`CLAUDE.md` はプロジェクト固有の情報を記述するファイルとして、配布の管理対象外。
プロジェクトごとに手動で管理する (`/init` コマンドなどを活用するのも手)。

### 更新

ルールファイルは頻繁に更新される可能性があるため、定期的にインストールコマンドを再実行して最新化することを推奨する。
再実行時は既存ファイルのfrontmatterを維持したままbodyのみ更新される。

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

ルールだけではカバーしきれない領域（hookによる編集検査など）を補うためのプラグインを提供する。
本リポジトリ自体をClaude CodeのPlugin Marketplace (`ak110-dotfiles`) として登録できるようにしてあり、今後もプラグインを追加する可能性がある。

プラグインは原則project scopeで各プロジェクトに導入する。
プロジェクトの `.claude/settings.json` に `enabledPlugins` と `extraKnownMarketplaces` を設定する。
開発者がフォルダーをtrustした時にClaude Codeがインストールを自動で提案する。
設定方法は [docs/guide/claude-code.md](claude-code.md) のセットアップ手順を参照。

### 前提条件

プラグインは [uv](https://docs.astral.sh/uv/) に依存する。
事前にインストールしておく必要がある。

### 自動更新の有効化

非公式のPlugin Marketplaceはデフォルトで自動更新が無効のため、初回のみ手動で有効化する。

1. Claude Code内で `/plugin` を実行
2. `Marketplaces` タブで `ak110-dotfiles` を選択
3. `Enable auto-update` を選択

### プラグイン詳細

#### agent-toolkit

好ましくない編集や冗長なBash呼び出しを `PreToolUse` 段階で検出・制御するプラグイン。
コードベースの破壊や、Claude Codeの訓練データ由来の誤った思い込みによる事故を未然に防ぐことを目的としている。
名前に "edit" を含むが、スコープは `Write` / `Edit` / `MultiEdit` / `Bash` に加えて `Read` まで含む（後述のmkdir自動許可とRead前のエンコーディング検査のため）。

主なチェック内容は以下。

- 文字化け (U+FFFD) を含む `Write` / `Edit` / `MultiEdit` をブロック
- LF改行のみの `.ps1` / `.ps1.tmpl` への書き込みをブロック（Windows PowerShell 5.1対策）
- ロックファイルや `.venv/` / `node_modules/` など自動生成物の手編集をブロック
- シークレットらしき値の書き込みや、ホームディレクトリの絶対パスのハードコードを警告・ブロック
- ターミナル破壊バイト（UTF-16/32 BOM・NUL・ESC・非許可C0制御文字・非UTF-8）を含むファイルに対する `Read` をブロック
  - Shift-JISなど非UTF-8テキストやバイナリをClaude CodeがReadすると、結果がターミナルへ流れて表示が崩れる事故を防ぐ
  - 画像 / PDF / ノートブック（`.png` / `.pdf` / `.ipynb` 等）はClaude本体の専用経路に乗るため検査対象外
- 既存ディレクトリ `~/.claude/plans` に対する冗長な `mkdir -p` を自動許可 (Bash)
  - plan modeでClaudeがプラン ファイル書き込み前に走らせる冗長な `mkdir` による
    許可確認プロンプトを抑止する。対象がランタイムで既存ディレクトリの場合のみ許可するため、
    許可される呼び出しは常にno-op相当（ファイルシステム変更なし）

同梱スキルとして以下を持つ。

- `tidy-unpushed-commits`: 複数の未プッシュコミットを慎重で再現性のある手順で整理する（squash・reorder・メッセージ書き直し）。
  退避refとツリー差分検証で最終ツリーの同一性を機械的に担保し、乱暴な`git reset`は使わない。
  直前コミットへのamendや特定コミットへのfixupで済む場合はagent.mdの軽量パターンに自動分岐する。
  `/tidy-unpushed-commits`スラッシュコマンドで明示的に呼び出せる
- `pyfltr-usage`: pyfltrの使い方・JSONL出力の解釈方法・サブコマンドの使い分けを参照できるリファレンス。
  日常的なpyfltr利用に必要な情報を自己完結的に含み、詳細な設定情報が必要な場合のみllms.txtから個別ページを取得する構成。
  `/pyfltr-usage`スラッシュコマンドで明示的に呼び出せる
- `pytilpack-usage`: pytilpackのモジュール構成・代表的な使い方・APIドキュメント参照方法のリファレンス。
  llms.txtを段階的に取得して必要なモジュールのAPI情報を参照する構成。
  `/pytilpack-usage`スラッシュコマンドで明示的に呼び出せる

### 移行: edit-guardrails → agent-toolkit

旧プラグイン `edit-guardrails` は `agent-toolkit` に改名・統合された。
`edit-guardrails` がインストール済みの場合は以下のコマンドで削除する。

```bash
claude plugin uninstall edit-guardrails@ak110-dotfiles
```

dotfiles利用者は `update-dotfiles` を実行すれば自動的に削除される。
