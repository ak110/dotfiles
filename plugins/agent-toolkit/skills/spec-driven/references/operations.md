# 運用補足（中断・再開・導入チェックリスト）

通常フロー外で参照する手順をまとめる。
5フェーズの通常手順は`workflow.md`を参照する。
本ファイル中の具体パス表記は既定値であり、プロジェクトのドキュメントで別の配置が規定されている場合はそちらに読み替える（`spec-driven`スキルSKILL.mdの「配置の既定と上書き」節を参照）。

## 中断・再開

セッションを跨ぐ場合や途中で中断した場合の進め方。

- `.working.md`と`~/.claude/plans/{自動生成ファイル名}.md`（Designでは3aと3cの2本）がセッション間の状態を保持する
- 再開時は関連ファイルを読み直し、`.working.md`の「現在のフェーズ」から続きを進める
- TaskCreateの残タスクを確認し、未完了分を先頭から再開する
- フェーズ番号は`.working.md`の先頭に明記する。Designでは`3a`・`3b`・`3c`のサブフェーズまで記録し、plan mode内外の切り替え位置を再開時に特定できるようにする
- 開発中配置（`docs/v{next}/`配下）が存在する場合はそこが作業対象となる。恒常配置（`docs/features/`・`docs/topics/`）は参照専用で、改修作業中は直接編集しない（昇格前に恒常配置と実装が乖離するのを避けるため）

## 前段階・後段階の別スキル

- 既存プロジェクトへのspec-driven導入: `spec-driven-init`スキルを起動する。既存コード・ドキュメント・既存`docs/v*/`群から`docs/features/`・`docs/topics/`配下の恒常配置の初版を整備する
- リリース完了後の昇格: `spec-driven-promote`スキルを起動する。開発中バージョンディレクトリの作業版を恒常配置へ移動し、開発中ディレクトリを削除する

どちらも手動トリガー専用で、引数無しで起動する。

## 導入・定期確認チェックリスト

初回導入時または定期確認時に、小さな仮題材（例: READMEへの節追加程度）でIntakeからCleanupまでを1回通す。

- [ ] Intakeで機能名・バージョン・新規追加か既存改修かの区分の確認が行われる
- [ ] 新規追加時は`docs/v{next}/`配下に作業版`.md`と`.working.md`が生成される。既存改修時は恒常配置（`docs/features/`または`docs/topics/`）の該当`.md`がコピー元となる
- [ ] `docs/v{next}/README.md`が作成／更新され、機能一覧に当該機能のエントリが追加されている
- [ ] Exploreで`spec-researcher`が呼び出され、恒常配置（`docs/features/`・`docs/topics/`）と開発中配置（`docs/v{next}/`）の両方を参照した並列調査結果が`.working.md`に集約される
- [ ] Design 3aで`plan-mode`が呼び出され、大枠の計画ファイルが`~/.claude/plans/`配下に作成される
- [ ] Design 3aでcodexレビューが実行され、大枠合意ゲートを経て`ExitPlanMode`される
- [ ] Design 3bで開発中`.md`が具体化され、必要なら横断ドキュメントが切り出される
- [ ] Design 3cで`EnterPlanMode`により再突入し、実装計画ファイルが作成されcodexレビューを経て`ExitPlanMode`される
- [ ] Tasksでタスクが`.working.md`とTaskCreateの両方に登録される
- [ ] Implementで`spec-implementer`が呼び出される
- [ ] Cleanup手順6・7（最終反映・`README.md`最新化確認）が`spec-reviewer`実行より前に完了している
- [ ] `spec-reviewer`が整合性観点（`README.md`反映・恒久ドキュメント間の明示矛盾・参照コメント指し先の実在と関連性・既存改修時のBefore/After整合）も検査したことを出力から確認する
- [ ] Cleanupで`.working.md`が削除され、開発中`.md`のみ残る
- [ ] 参照コメントは恒久`.md`のみを指し、`.working.md`を参照していない。開発中配置のパスを指すものは昇格時に恒常配置のパスへ書き換える想定で作る
- [ ] 全工程で各フェーズ終端のユーザー確認ゲートが機能する
- [ ] 昇格時（`spec-driven-promote`実行後）に開発中バージョンディレクトリが削除され、恒常配置のみが残る
