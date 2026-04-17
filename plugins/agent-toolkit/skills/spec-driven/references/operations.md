# 運用補足（中断・再開・導入チェックリスト）

通常フロー外で参照する手順をまとめる。
5フェーズの通常手順は`workflow.md`を参照する。

## 中断・再開

セッションを跨ぐ場合や途中で中断した場合の進め方。

- `.working.md`と`~/.claude/plans/{自動生成ファイル名}.md`（Designでは3aと3cの2本）がセッション間の状態を保持する
- 再開時は関連ファイルを読み直し、`.working.md`の「現在のフェーズ」から続きを進める
- TaskCreateの残タスクを確認し、未完了分を先頭から再開する
- フェーズ番号は`.working.md`の先頭に明記する。Designでは`3a`・`3b`・`3c`のサブフェーズまで記録し、plan mode内外の切り替え位置を再開時に特定できるようにする

## 導入・定期確認チェックリスト

初回導入時または定期確認時に、小さな仮題材（例: READMEへの節追加程度）でIntakeからCleanupまでを1回通す。

- [ ] Intakeで機能名・バージョンの確認が行われる
- [ ] 恒久`.md`と`.working.md`が`docs/v{version}/`配下に生成される
- [ ] `docs/v{version}/README.md`が作成／更新され、機能一覧に当該機能のエントリが追加されている
- [ ] Exploreで`spec-researcher`が呼び出され、並列調査結果が`.working.md`に集約される
- [ ] Design 3aで`plan-mode`が呼び出され、大枠の計画ファイルが`~/.claude/plans/`配下に作成される
- [ ] Design 3aでcodexレビューが実行され、大枠合意ゲートを経て`ExitPlanMode`される
- [ ] Design 3bで恒久`.md`が具体化され、必要なら横断ドキュメントが切り出される
- [ ] Design 3cで`EnterPlanMode`により再突入し、実装計画ファイルが作成されcodexレビューを経て`ExitPlanMode`される
- [ ] Tasksでタスクが`.working.md`とTaskCreateの両方に登録される
- [ ] Implementで`spec-implementer`が呼び出される
- [ ] Cleanupで`.working.md`が削除され、恒久`.md`のみ残る
- [ ] Cleanupで`docs/v{version}/README.md`の機能一覧・横断ドキュメント一覧が最新化されている
- [ ] 全工程で各フェーズ終端のユーザー確認ゲートが機能する
- [ ] 参照コメントは恒久`.md`のみを指し、`.working.md`を参照していない
