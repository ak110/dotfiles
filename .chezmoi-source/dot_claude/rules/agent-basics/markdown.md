---
paths:
  - "**/*.md"
  - "**/*.mdx"
---

# Markdown記述スタイル

- `**`は強調したい箇所のみとし、箇条書きの見出しなどでの使用は禁止する
- できるだけmarkdownlintが通るように書く
  - 特に注意するルール:
    - MD022 - Headings should be surrounded by blank lines
    - MD031 - Fenced code blocks should be surrounded by blank lines
    - MD040 - Fenced code blocks should have a language specified
- 1文ごとに改行する（一行が長くなって読みづらく、差分レビューも難しくなるため）
  - 機械的な改行で文の区切りが不自然になる場合は、短文を一文にまとめるなど書き換えでの対応も検討する
- 図はMermaid記法で書く
- 別のMarkdownファイルへのリンクは用途によって書き分ける
  - Markdownソースのまま読まれる想定のファイル（`README.md`・`CLAUDE.md`・GitHub閲覧前提のdocsなど）では、`[プロジェクトルートからのパス](記述個所からの相対パス)`で書く。閲覧者がリンク先のパスを一目で把握できる利点がある
    - 例: `docs/api.md` から `docs/guide/setup.md` へリンク → `[docs/guide/setup.md](guide/setup.md)`
  - ドキュメント生成ツール（mkdocs・Sphinx・Docusaurusなど）でHTMLほかの形式に変換される想定のファイルでは、リンクテキストをページタイトルや自然な表現にする。変換後の利用者には内部パスが無意味なため
    - 例: `[セットアップ手順](guide/setup.md)`
