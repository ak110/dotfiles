---
name: writing-standards
description: >
  Markdown・README・技術文書・API文書などのドキュメント、
  各種プログラムやスクリプトのコメントの新規作成・修正・計画・レビュー時に最初に必ず呼び出す。
# 編集時の注意点:
# コード編集時はcoding-standardsと本スキルの両方を読み込むが、
# ドキュメントのみの編集時はwriting-standardsのみを読み込むことに注意。
# コーディングエージェント向け文書固有のガイドはagent-standardsスキルへ分離した。
# 本スキルではClaude Code固有事項を扱わない。
---

# ドキュメント品質

一般ドキュメント（Markdown・README・技術文書・API文書、コメント類）の品質指示。
コーディングエージェント向け文書も対象に含むが、その編集時は`agent-toolkit:agent-standards`を併用する。

## 構成・構造

- 対象読者を明確にする
- 冒頭にドキュメントの目的・概要を置き、読者が「読むべきか」を即座に判断できるようにする
- 前提条件（必要な知識・ツール・環境）は本題の前に配置する
- 見出しの階層は3段（`##` / `###` / `####`）までを目安とする
- 関連する内容は近くにまとめ、読者が文書内を往復しなくて済むようにする
- コマンド例やコードスニペットはコピー&ペーストでそのまま動くことを前提に書く
- 外部リソースへのリンクは、リンク切れリスクを考慮して要点を本文にも記載する

## 改訂・保守

- 追記する内容の粒度が既存記述と整合しているか確認し、必要に応じて既存部分や章構成も変更する
- 陳腐化した記述は積極的に削除する。「古い情報が残る」ことは「情報がない」より有害
- SSOTを維持する。重複が避けられない場合はどちらが正かを明記する
- ドキュメントの変更がコードの変更と連動する場合は、同じコミットまたはPRに含める
- 同一ファイル内に本文と例（フォーマット例・コードブロック）が併存する場合、
  本文を変更したら例も合わせて更新し、片方だけ更新されて矛盾が残らないようにする

## 口語表現チェック

- 口語的な日本語表現の混入を防ぐため、`scripts/check_colloquial.py`の実行を必須とする

    ```sh
    uv run --script path/to/writing-standards/scripts/check_colloquial.py path/to/file.md
    uv run --script path/to/writing-standards/scripts/check_colloquial.py path/to/dir
    ```

  - 検出範囲: `.md`・`.py`・`.txt`・`.yaml`・`.yml`・`.toml`
    - コードブロック内のコメントも含む

コンテキスト汚染を避けるため、以下のファイルはメインエージェントから直接Readしない。

- `agent-toolkit/skills/writing-standards/references/tone-examples.md`
- `agent-toolkit/scripts/_colloquial_words.txt`

確認が必要な場合はExploreサブエージェント経由で参照する。修正が必要な場合は`plan-implementer`経由で行う。

## Markdown記述スタイル

行幅・句点位置などの細則はmarkdownlint・textlintで自動検証される。
頻出textlint違反パターンは`references/textlint-violations.md`を参照する。

- `**`は強調したい箇所のみとし、箇条書きの見出しなどでの使用は禁止する
- markdownlintが通るように書く。特に注意するルール:
  - `MD022`: Headings should be surrounded by blank lines
  - `MD031`: Fenced code blocks should be surrounded by blank lines
  - `MD040`: Fenced code blocks should have a language specified
- 1行の表示幅は半角換算で127を上限とする（全角=2、半角=1）
  - frontmatterが長くなる場合はYAML複数行記法（`>`など）で収める
  - 1文ごと（`。`ごと）に改行する
  - 機械的チェック: `scripts/check_line_width.py`で検査できる
    - `uv run --script path/to/writing-standards/scripts/check_line_width.py path/to/file.md`
  - 例外: コードブロック・表

- 箇条書きは1項目1文で書き、120字を超える単文は別項目へ分割する
  - textlintの`ja-technical-writing/sentence-length`や`preset-jtf-style 1.1.3.箇条書き`の違反を避けるため
- 図はMermaid記法で書く
- 別のMarkdownファイルへのリンクは用途によって書き分ける
  - Markdownソースのまま読まれる想定（`README.md`・`CLAUDE.md`・GitHub閲覧前提のdocsなど）:
    `[プロジェクトルートからのパス](記述個所からの相対パス)`形式（閲覧者がパスを一目で把握できる）
    - 例: `docs/api.md`から`docs/guide/setup.md`へリンク → `[docs/guide/setup.md](guide/setup.md)`
  - HTMLに変換される想定（mkdocs・Sphinx・Docusaurus等）: 自然な表現でリンクテキストを書く
    - 例: `[セットアップ手順](guide/setup.md)`
- テーブルは列数が多い場合や内容が長い場合は箇条書きへの変換を検討する
  - 特定列のみ長文化する場合はキー単語に留めて表下の補足文へ退避する

## 技術文書の書き方

- 手順書（セットアップ・デプロイ・移行など）:
  - 番号付きリストで手順を示す。各ステップは1つの動作に限定する
  - 前提条件・事前準備を冒頭に明記する
  - 成功時の期待結果を各ステップまたは末尾に記載する
  - 失敗時の対処やロールバック手順も含める
- トラブルシューティング: 症状 → 原因 → 対処の3要素で構成する。よくある問題から順に並べ、
  エラーメッセージは検索しやすいように正確に引用する
- API文書: エンドポイント・パラメーター・レスポンスを網羅し、リクエスト／レスポンスの具体例とエラーケースも記載する
- コミットメッセージ: 読者はエンドユーザー
  - 内部リファクタリングなどの詳細は不要
  - プロジェクト方針があればそれに従う

## ディレクトリ構成

OSSプロジェクトの典型的なドキュメント配置（プロジェクト方針がある場合はそちらに従う）。

- ルート直下: `README.md`、`LICENSE`
- `docs/`配下: 種別ごとにサブディレクトリ（`docs/guide/`・`docs/development/`・`docs/api/`等）
- ファイル粒度: 1ファイル1トピック。1ファイル300行超で分割を検討する
- ファイル命名: ケバブケース（`setup-guide.md`）。定番ファイルは大文字の慣例に従う
- サブディレクトリの入口index: 静的サイトジェネレーター（mkdocs等）のnav機能で目次を自動生成できる場合は不要。
  手動メンテのリンク集index.mdは陳腐化しやすいため設けない

## README規約

READMEはプロジェクトのトップレベルに配置し、初見の読者が最初に参照する。

- 標準構成パターン（プロジェクト方針がない場合の例。規模と性質に応じて取捨選択する）:
  1. プロジェクト名と一文の概要
  2. 主な機能・特徴（箇条書き）
  3. インストール・セットアップ手順
  4. 基本的な使い方（コード例やコマンド例）
  5. ドキュメントへのリンク
- `CLAUDE.md`との役割分担: READMEは人間の読者向け、`CLAUDE.md`はClaude Code向け。
  両者で重複する情報はREADMEを正とし、`CLAUDE.md`からはREADMEを参照するか要点のみ再掲する
