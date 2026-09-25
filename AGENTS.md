# AGENTS.md: dotfiles

本リポジトリはchezmoi管理のdotfilesリポジトリであり、`.chezmoi-source/`配下を`~/.*`にデプロイする。
多数の小規模なコマンドラインツールや、Claude Code用の共有設定（ルール・プラグイン）も持つ。

## 詳細の参照先

次の各文書は経緯、根拠及び構造の記録であり、実行時に適用する規範は`agent-toolkit/rules/`配下と各スキルが定める。

- リポジトリ全体の構成・配布対象と開発対象の区別・プラットフォーム対応・bash補完運用・
  PowerShellスクリプト注意事項・ホーム配下編集前の確認手順:
  [docs/development/architecture.md](docs/development/architecture.md)
- 運用機能の詳細（`sync_generated_files.py`の起動形・tmux自動アタッチ・UWI未回答表示・
  常駐サービス・Windows電源設定・post-applyキャッシュ・chezmoiの命名規則）:
  [docs/development/operations.md](docs/development/operations.md)
- 過去のAWIから確定した方針・意向: [docs/development/concepts.md](docs/development/concepts.md)
- 再発防止の判断材料となる障害・欠陥: [docs/development/incidents.md](docs/development/incidents.md)
- 規範の条文が根拠とする検証記録の日付・版数・再検証手段:
  [docs/development/audit-records.md](docs/development/audit-records.md)

## 本リポジトリのスキル

次のスキルはリポジトリ直下の`.claude/skills/`配下にあり、配布対象外である。
起動する場面は各スキルの`description`が定める。

| スキル | 扱う範囲 |
| --- | --- |
| `dotfiles-development` | テスト、整形及び依存更新の手順と、振り返りの参照文書の位置 |
| `dotfiles-release` | `develop`と`master`のリリース運用、日次リリースの判定と実施 |
| `dotfiles-repo-layout` | ロールとファイル群の対応、配布元と配布先の対応、変更した規範の自セッション適用 |
| `agent-toolkit-edit` | `agent-toolkit/`配下と`.claude-plugin/marketplace.json`の編集、version bump、権限設定の配置 |
| `pytools-edit` | `pytools/`・`scripts/`・`bin/`・`rust/`配下の編集 |
| `sync-platform-pair` | Linux/Windowsペアファイルの同期 |
| `merge-pr` | PRのマージと、マージ後のbranch同期、CI及び必要なReleaseの検収 |
