# 追従台帳：品質基準とスキル

## 共通品質基準の参照をagent-basicsへ移す

- 項目ID: project-quality-references-agent-basics
- 実施日: 2026年10月
- 内容: プロジェクトの品質資料の参照、プラグイン設定、導入案内、読込契機をagent-basicsへそろえる
- 理由: 成果物の品質基準を、WIや委譲などの運用から独立して利用できるようにする
- 当てはまる条件: `AGENTS.md`や固有スキルが、agent-toolkitの旧品質資料を参照する
- 適用方法: 作成・設計・テストは`agent-basics:writing-standards`、レビューは`agent-basics:review-standards`へ接続し、各ホストの導入設定と既存の固有資料への参照を確認する
- 参照先: `github.com/ak110/pyfltr`の`AGENTS.md`、`.claude/settings.json`、`.codex/config.toml`、`docs/development/development.md`
- 適用済みの見分け方: 共通品質基準の提供元と導入案内がagent-basicsで一致し、専用担当を含め必要な資料へ到達できる

## 手順スキルを動詞始まりの名前にする

- 項目ID: procedural-skill-leading-verb
- 実施日: 2026年10月〜11月
- 内容: 順序だった作業手順を中心に持つスキルを、動詞で始まる名前へ改める
- 理由: 名前から何をするスキルかを読み取れるようにする
- 当てはまる条件: プロジェクト固有のスキルに、作業手順を中心とするものがある
- 適用方法: 手順を担うスキルを分類し、ディレクトリ名・frontmatter・呼出名・参照を一緒に変え、基準や契約を提供するスキルと機械用の別識別子は区別する
- 参照先: `github.com/ak110/pyfltr`の`.claude/skills/add-pyfltr-tool/SKILL.md`と`AGENTS.md`
- 適用済みの見分け方: 手順スキルの名前が動詞で始まり、ディレクトリ・`name`・呼出元が一致し、旧名の呼出しや不要な中継が残らない
