# 開発者向けガイド

本リポジトリ自体の開発に関するドキュメントである。
開発の手順と構成、記録の索引、検証と判定の記録の順に並べる。

## 開発

- [docs/development/development.md](development.md): 開発環境セットアップ・コマンド・サプライチェーン攻撃対策・リリース手順
- [docs/development/architecture.md](architecture.md): リポジトリ構成・プラットフォーム対応・補完運用・agent-toolkitの3形式配布
- [docs/development/operations.md](operations.md): 運用機能のホスト固有事項と詳細仕様
- [docs/development/commit-types.md](commit-types.md): コミットメッセージのtypeの判定例
- [docs/development/internals.md](internals.md): 実装者向けの内部仕様（agents_serverの共有状態、セッション状態とフラグ、委譲先の調査手順、環境変数）の索引
- [docs/development/delegation-maintenance.md](delegation-maintenance.md): 委譲の仕組みを保守する人だけが使う事実（OpenCodeのターン起動、旧モデル設定の移行）
- [docs/development/read-triggers-contract.md](read-triggers-contract.md): エージェント向け文書の読込表と、委譲先の起動の本文へ資料の全文を入れる配送の契約

## 記録の索引

- [docs/development/design.md](design.md): 機構の設計記録の索引。
  主題別の本文から目的・構造の理由・知識境界・却下案を読む
- [docs/development/concepts.md](concepts.md): 過去のAWIから確定した方針・意向
- 障害と撤去した仕組みの経緯は、是正・撤去のcommit本文に`再発:`・`撤去:`の行で残し、`git log --grep`で引く（2026年11月22日に障害事例集と撤去台帳を削除した。削除前の内容はGitの履歴にある）。
  新しい仕組みを加える前に照合する

## 検証と判定の記録

- [docs/development/audit-records.md](audit-records.md): 規範の条文とそれを実装するコードが根拠とする、実際に検証した日付・版数・再検証手段
