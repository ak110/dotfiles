# 開発者向けガイド

本リポジトリ自体の開発に関するドキュメントである。
開発の手順と構成、記録の索引、検証と判定の記録の順に並べる。

## 開発

- [docs/development/development.md](development.md): 開発環境セットアップ・コマンド・サプライチェーン攻撃対策・リリース手順
- [docs/development/architecture.md](architecture.md): リポジトリ構成・プラットフォーム対応・補完運用・agent-toolkitの3形式配布
- [docs/development/operations.md](operations.md): 運用機能のホスト固有事項と詳細仕様
- [docs/development/commit-types.md](commit-types.md): コミットメッセージのtypeの判定例

## 記録の索引

- [docs/development/design.md](design.md): 機構の設計記録の索引。主題別の本文から目的・構造の理由・知識境界・却下案を読む
- [docs/development/concepts.md](concepts.md): 過去のAWIから確定した方針・意向
- [docs/development/incidents.md](incidents.md): 再発防止の判断材料となる障害・欠陥

## 検証と判定の記録

- [docs/development/audit-records.md](audit-records.md): 規範の条文とそれを実装するコードが根拠とする、実際に検証した日付・版数・再検証手段
- [docs/development/norm-restructure/README.md](norm-restructure/README.md): 規範の全体の再構築で全条文を判定した台帳と、守る障害・方針の逆引き表（作成時点の固定の記録）
