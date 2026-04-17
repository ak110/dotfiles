# 運用補足（中断・再開・導入チェックリスト）

通常フロー外で参照する手順をまとめる。
5フェーズの通常手順は`workflow.md`を参照する。

## 中断・再開

セッションを跨ぐ場合や途中で中断した場合の進め方。

- `.working.md`と`~/.claude/plans/spec-driven-{機能名}.md`がセッション間の状態を保持する
- 再開時は両ファイルを読み直し、`.working.md`の「現在のフェーズ」から続きを進める
- TaskCreateの残タスクを確認し、未完了分を先頭から再開する
- フェーズ番号を`.working.md`の先頭に明記しておくと再開時の混乱を避けられる

## 導入・定期確認チェックリスト

初回導入時または定期確認時に、小さな仮題材（例: READMEへの節追加程度）でIntakeからCleanupまでを1回通す。

- [ ] Intakeで機能名・バージョンの確認が行われる
- [ ] 恒久`.md`と`.working.md`が`docs/v{version}/`配下に生成される
- [ ] Exploreで`spec-researcher`が呼び出され、並列調査結果が`.working.md`に集約される
- [ ] Designで`plan-mode`が呼び出され、`~/.claude/plans/spec-driven-*.md`が作成される
- [ ] Designでcodexレビューが実行される
- [ ] Tasksでタスクが`.working.md`とTaskCreateの両方に登録される
- [ ] Implementで`spec-implementer`が呼び出される
- [ ] Cleanupで`.working.md`が削除され、恒久`.md`のみ残る
- [ ] 全工程で各フェーズ終端のユーザー確認ゲートが機能する
- [ ] 参照コメントは恒久`.md`のみを指し、`.working.md`を参照していない
