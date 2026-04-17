---
name: claude-meta-rules
description: CLAUDE.md・.claude/rules/・.claude/skills/・hooksなどClaude Code設定系ファイルの記述ガイドライン。CLAUDE.md、`.claude/rules/`配下の.md、`.claude/skills/`配下のSKILL.md、プラグインのhook設定やhookスクリプト、`plugin.json`・`marketplace.json`などClaude Codeの設定ファイル群を編集・追加する時に呼び出すこと。Claudeの訓練データに無い新機能仕様の補完と、コンテキスト汚染を避ける記述原則をまとめている。対象別の詳細は references/ 配下を必要に応じて読む。
user-invocable: true
---

# CLAUDE.md・ルール・スキル・フック記述ガイドライン

Claude Code設定ファイル群はLLMのコンテキストへ直接投入される。
記述した内容がそのまま生成候補に影響するため、以下の共通原則と対象別ガイドに従う。

## CLAUDE.md・ルール・スキルの使い分け

| 項目 | CLAUDE.md | .claude/rules/ | .claude/skills/ |
| --- | --- | --- | --- |
| 読み込み | 常時 | 起動時 or ファイル操作時 | オンデマンド（呼び出し時） |
| 用途 | プロジェクト全体の指示 | トピック別・ファイル種別の規約 | 特定タスクの手順・ワークフロー |
| スコープ制限 | 不可 | `paths`で可能 | 不可 |
| コンテキスト消費 | 常時 | 常時 or 条件付き | 呼び出し時のみ |
| 適した内容 | ビルドコマンド、アーキテクチャ概要 | コーディング規約、テスト方針 | デプロイ手順、特定ワークフロー |

## 対象別リファレンスの使い分け

編集対象に応じて、以下のreferencesを読み込む。

- `references/claude-rules.md`: `.claude/rules/` 配下のルールファイル編集ガイドライン（`paths` frontmatter、ファイル構成、記述のコツ）
- `references/claude-skills.md`: `.claude/skills/` 配下のスキル編集ガイドライン（ディレクトリ構造、progressive disclosure、YAML frontmatter、変数）
- `references/claude-hooks.md`: hook・プラグインスクリプトの出力フィールドガイドライン（`systemMessage` / `reason` / `additionalContext` の使い分け、メッセージ記述言語）

複数対象を編集する場合は該当するreferencesを必要な分だけ読む。

### 公式ドキュメント

referencesに記載のない仕様の詳細や新機能を確認する必要がある場合、以下の公式ドキュメントをWebFetchで取得する（URLに`.md`サフィックスを付与するとMarkdown形式で取得できる）。

| ページ | URL | 主な内容 |
| --- | --- | --- |
| Memory | `https://code.claude.com/docs/ja/memory.md` | CLAUDE.md、`.claude/rules/`の書き方、`@import`構文 |
| Skills | `https://code.claude.com/docs/ja/skills.md` | スキルのfrontmatter全フィールド、変数、配置場所 |
| Hooks | `https://code.claude.com/docs/ja/hooks.md` | 全イベント一覧、matcher、type、出力フィールド |
| Plugins | `https://code.claude.com/docs/ja/plugins.md` | プラグイン構造、plugin.json、コンポーネント構成 |

## 共通の記述原則（全対象）

### コンテキスト汚染の回避

- 不適切な表現や禁止パターンの具体例をそのまま書かない（コンテキストに混入し生成されやすくなるため）
  - 具体例が必要な場合は抽象化した表現にとどめる
- 禁止事項と推奨事項を明確に分離する（セクションや箇条書きの階層で区切る）

### 理由を添える

- 「なぜそうするか」を添える。モデルは理由を理解すると想定外のケースにも適切に対応できる
- 具体的な推奨例を含めると遵守率が上がる
- 矛盾する指示がないか定期的に確認する（複数ファイル間の矛盾はモデルが任意に選択する原因になる）

### システムプロンプト・組み込みツールとの整合性

- 表現を極力システムプロンプトと合わせる（訳語として自然な用語を使用する）
  - 例えばユーザーが「〇〇と追記して」と要求した場合も、システムプロンプトで定義された用語を優先する（文章が大幅に変わる場合は確認をとる）
- 極力システムプロンプトと矛盾しない、かつ重複しない指示にする
  - やむを得ずシステムプロンプトと矛盾する指示を書く場合はその旨注記する

### ファイルサイズの管理

- CLAUDE.md本体は60〜200行を目安にする。超過する場合はトピック別に `.claude/rules/` 配下へ分離する（常時読み込まれるためコンテキスト消費が大きい）
- 長文を書く必要がある場合は、重要度の高い指示を冒頭近くに配置する（モデルは長文の中盤以降を軽視しやすい傾向があるため）
- 条件付きで適用される指示は `<important if="...">` タグで囲み、該当条件が成立するときのみ重視される形にする

## Gotchas

- システムプロンプト用語と訳語が揺れがち。`.claude/` 配下の記述では、システムプロンプトで定義された用語を優先する
- 複数ファイルに同一トピックを書くと矛盾が生まれやすい。SSOTを置き、他のファイルは参照に留める
- 禁止事項を具体的な悪い例で示すとコンテキスト汚染を招きがち。抽象化した表現で禁止の意図を伝える
