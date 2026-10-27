# AGENTS.md: dotfiles

このリポジトリはchezmoiで管理するdotfilesリポジトリであり、`.chezmoi-source/`配下のファイルを`~/.*`へ展開する。
また、多数の小さなコマンドラインツールや、Claude Code用の共有設定（ルールやプラグイン）も備えている。

この作業ツリーでPythonコードを実行するときは、標準ライブラリだけで済む簡単な確認には`python3`を使い、リポジトリの依存関係やモジュールを利用する処理には`uv run --frozen python`を使う。常時規範にある`python -c`は起動形式の例示であり、実行ファイル`python`が環境に存在することを保証するものではない。

## 詳細な参照先

以下の各文書には、これまでの経緯や根拠、構造の記録をまとめている。実際に作業する際に従う規範は、`agent-toolkit/rules/`配下や各スキルを参照する。

- 開発者向けドキュメントの一覧: [docs/development/index.md](docs/development/index.md)
- リポジトリ全体の構成、配布対象と開発対象の区別、プラットフォーム対応、bash補完の運用、PowerShellスクリプトの注意事項、agent-toolkitの3形式での配布と`agents_server` MCPの配置、ホームディレクトリ配下を編集する前の確認手順: [docs/development/architecture.md](docs/development/architecture.md)
- 運用機能の詳細（`sync_generated_files.py`の起動形式、tmux自動アタッチ、常駐サービス、Windowsの電源設定、post-applyキャッシュ、chezmoiの命名規則）: [docs/development/operations.md](docs/development/operations.md)
- 機構の目的、構造の理由、知識境界、却下した案を記録した設計記録の索引: [docs/development/design.md](docs/development/design.md)
- 過去のAWIを通じて確定した方針や意向: [docs/development/concepts.md](docs/development/concepts.md)
- 再発防止の判断材料となる障害や欠陥の記録: [docs/development/incidents.md](docs/development/incidents.md)
- 各規範の条文が根拠としている検証記録の日付、版数、再検証の手順: [docs/development/audit-records.md](docs/development/audit-records.md)

## 本リポジトリのスキル

以下のスキルはリポジトリ直下の`.claude/skills/`配下にあり、外部への配布対象ではない。
それぞれのスキルを起動する場面は、各スキルの`description`に従う。

| スキル | 扱う範囲 |
| --- | --- |
| `dotfiles-development` | テスト、コード整形、依存関係の更新手順、リポジトリ全体のテスト配置、コミットメッセージの方針、ホスト本体のバイナリ検索手順、振り返り用ドキュメントの参照先 |
| `dotfiles-release` | `develop`と`master`のリリース運用、日次リリースの判定と実施 |
| `dotfiles-repo-layout` | ロールとファイル群の対応関係、配布元と配布先の対応関係 |
| `dotfiles-norm-edit` | エージェント向け文書の改訂、変更した規範の自セッションへの適用、process-wiのメインが記述する`プロジェクト規範の指定`、規範を削除・縮小する際の消失確認 |
| `agent-toolkit-edit` | `agent-toolkit/`配下と`.claude-plugin/marketplace.json`の編集、バージョン更新と編集手順、権限設定の配置 |
| `pytools-edit` | `pytools/`、`scripts/`、`libexec/`、`bin/`、`rust/`配下の配置規約と実装規約 |
| `sync-platform-pair` | LinuxとWindowsのペアファイルの同期 |
| `merge-pr` | PRのマージ、マージ後のブランチ同期、CIおよび必要なリリースの検収 |

## 成果物の品質基準

本リポジトリでは`agent-basics`を適用する。コード、ドキュメント、設定の作成や変更では`agent-basics:writing-standards`を用い、レビューや指摘の採否判断では`agent-basics:review-standards`を適用する。また、WI、委譲、認可、完了報告の運用は`agent-toolkit`に従う。
