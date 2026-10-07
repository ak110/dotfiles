# ホスト機能の公式リファレンス

本書はスキルの新規作成、hookの実装、auto modeのカスタムルールの編集およびホスト機能の可否・入出力契約の判定で、Claude Codeの仕様を確かめる一次資料と参照の手順を示す。

## 公式リファレンス（Claude Code）

スキル新規作成・hook実装、ホスト機能の可否・入出力契約の判定では公式マーケットプレイス（`anthropics/claude-plugins-official`）の
`skill-creator:skill-creator`・`plugin-dev`各スキルを参照する。
仕様は一次資料を優先して確認する。各スキルの`references/`に記載のない仕様と、記載と認識が相違する事項は公式ドキュメントで確認する。
参照先は`https://code.claude.com/docs/ja/`配下（`memory.md`・`skills.md`・`sub-agents.md`・
`hooks.md`・`plugins.md`・`plugins-reference.md`など）とする。
列挙にないページを参照する場合は、ドキュメントインデックス（`https://code.claude.com/docs/llms.txt`）を
取得して対象ページのURLを特定する。インデックスが列挙するURLは英語版
（`https://code.claude.com/docs/en/<page>.md`）のため、日本語版を読む場合は言語部分を`ja`へ置換して用いる。
`WebFetch`はページの特定と概要の把握に使用してよい。
