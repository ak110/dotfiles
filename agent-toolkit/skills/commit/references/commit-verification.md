# コミット：検証

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 保存済みの検証結果を再利用する前 | `agent-basics:check-execution/references/verification-scope-reruns.md` | | | |
| 未コミット検証の実行前とcommit後の適用確認前 | `../../../share/verification-evidence-observation.md` | | | |
| 取得版の異なる結果をレビューへ渡す前 | `../../../share/exec-review-recording-grounds.md` | | | |
| Git識別子・worktree・履歴・公開済みの状態を判定または操作する前 | `git-identifier.md` | | | |
| 公開工程またはリリース操作に着手する前 | `publish.md` | | | |
| amend、fixup、autosquash、rebaseまたはpush済み判定の前 | `history-rewrite.md` | | | |
| 実際にpushする前 | `push-and-ci.md` | 操作 | `bash:git push` | |
| コミットメッセージを書く前（計画ファイルやPR説明へコミットメッセージ案を書く前を含む） | `message.md` | | | |
| 既存未コミット差分を不採用として破棄する前 | `stash-cleanup.md` | | | |
| 一時ブランチ、`git stash`の退避、hookの退避ファイルまたは別パスへの複製を削除する前と、委譲先の完了報告が退避識別子か複製パスを開示したとき | `stash-cleanup.md` | | | |
| CI失敗を扱う前 | `agent-toolkit:commit/references/ci-failure-handling.md` | | | |
| 計画ファイルを作成する時と、公開インターフェース、エンドユーザー向けのメッセージ、永続データの形式を新設・変更する時 | `agent-toolkit:commit/references/design-time.md` | | | |
| テストコードを書く時とレビューする時、条件分岐と判定条件を新設・変更する時 | `agent-toolkit:commit/references/testing.md` | | | |

## 検証

**1.** 検証の重複を避けるため、初回の検証を選ぶ際に、コミットフックが実行するチェックと対象集合も含めて比較する。

フックの保存出力で計画の検証を省略・置換するのは、実行したツールごとの名前・対象ファイル・終了状態を観測し、その範囲が計画のコマンドの分を含むと確かめられた場合に限る。
フック単位の`pyfltr....Passed`などの集約表示や「lint」という種別名だけでは、この包含を判定できない。
実装担当とレビュー担当が不足分を判断できるよう、計画のツール集合、フックの実行ツール集合、対象ファイルの対応、不足分を検証記録へ残す。
同じ条件の検証はその保存出力を用い、フックの前後に重ねない。
不足するツールだけを別に実行し、フックによる更新があれば影響する成功結果を再取得する。
このまとめ方でもコミットフックを実行し、終了状態・診断・個別結果と最終内容の対応を確認する。

pyfltrを使うプロジェクトでは、`pyfltr list-runs`の各runの`commands`と`files`（対象件数）、`pyfltr show-run <run_id>`のツール別結果を確認手段にできる。
対象件数だけでは対象ファイルの包含を証明できないため、実行時の対象指定と結果も対応付ける。

**2.** 保存結果の再利用は、`agent-basics:check-execution/references/verification-scope-reruns.md`「実行条件と再実行」に沿って判断する。
取得版の対応は、`../../../share/exec-review-recording-grounds.md`「根拠の書き方」に従う。
確定後も有効な成功結果を活かし、影響する変更と未検証項目だけを追加検証する。

未コミットで後続へ渡す検証の前に、`../../../share/verification-evidence-observation.md`「検証時本文の保存」を適用する。
commitやフックの後は、その保存本文と最終内容を比較する。
cleanな版のOIDと適格な保存結果で対応を確認できる場合は、本文の重複保存や一律の全体再検証を要求しない。

**3.** 開発中は必要に応じて動作確認する。
レビューに提出する最終結果は、実装・テスト・入力と、すべてのコミットやコミットフックによる更新を終えた内容へ適用できることを確認する。
レビューへ進む際は、計画全体の変更範囲を網羅した検証結果と、それを取得した時点のバージョンを対応付ける。
