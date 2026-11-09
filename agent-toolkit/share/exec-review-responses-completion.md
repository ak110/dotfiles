# レビューイーの品質基準と運用：完了報告

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:review-standards/references/reviewee-verification.md` | 判断 | `read:agent-basics:*/skills/review-standards/references/reviewee-verification.md` |

## 完了報告

採否、修正方針、対応結果、および検証結果は、指定されたレビュー指摘管理表および成果物へ記録する。
委譲元へ返すメッセージの文面や返却値の形式については、該当する委譲元の`<役割名>.subagent.md`の規定に従う。
委譲先以外のエージェントは、本書の判定基準のみを適用して自身の作業工程を進める。

再レビューを優先する条件や、機械的な修正・詳細な実装の再レビューを省略できる条件は、`agent-basics:review-standards/references/reviewee-verification.md`「方式見直しと再レビュー」の定義に従う。
完成条件証拠で「未達」または「根拠不足」となった条件について再判定が必要な修正は、優先条件として扱う。
低リスク判定を追加の省略ルートとして利用する場合に限り、作業開始時点からのすべての変更パスを以下のコマンドで判定する。

判定は、対象worktree上で`atk run-script agent-doc-changes -- --repo <対象worktree> --review-risk <開始時点> <HEAD>`を実行して確認する。
変更されたすべてのパスが、エージェント向け文書に該当しない`.md`または`.md.tmpl`ファイルである場合は「低リスク」、それ以外の場合は「通常」と判定される。
Gitの管理外の変更を受け取ったラウンドは「通常」として扱う。
単なる回答の下書きを作成しただけでは、リスク段階を変更しない。
再レビューを省略する場合であっても、変更範囲の確認と`exec-review-responses.md`「公開可能性の検証」を実施し、採否、応答、および検証証拠をレビュー指摘管理表へ記録する。
通常段階で要件や仕様に関わる変更が含まれるラウンドにおいて、コード、設定、制御フロー、公開契約、期待値、実装方針を変更する修正や、判断に迷う修正をした場合は、必ず再レビューへ進む。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| レビューイーの品質基準と運用の基準を適用する前 | `exec-review-responses.md` | 判断 | `read:agent-toolkit:*/share/exec-review-responses.md` |
