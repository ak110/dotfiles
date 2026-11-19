# 統合時の完成条件判定

受領側は返却の完成条件証拠を、受領時（`agent-toolkit:atk-mode/playbooks/exec-review-loop.md`「完成条件証拠の受領確認」）にこの手順で確かめる。
統合時は、受理の記録（返却のパス、`レビューHEAD`、原要求と`review_contract`、指摘管理表）の変更が影響する条件と未検収の部分だけを判定し直す（記録が足りない範囲は全手順）。
受理できない行が1行でも残る間は、委譲元は統合の指示を保留し、レーン担当はmergeとAWIの完了処理を止める。

1. レビュー単位ごとに受理した最新の返却を全て読み、各返却が含む計画名と対応するAWIの集合を確かめる。
2. 返却の`レビューHEAD`が、統合対象の入力HEADか、レビュー修正で保持した修正前のHEAD（`agent-toolkit:atk-mode/playbooks/exec-review-loop.md`「レビュー修正」）と一致することを確かめる。
   HEADが変わり再レビューを省けない計画は、新しいHEADで全条件を判定し直した返却を求める。
3. 各WIの`review_contract`が反映項目ごとの対応を持ち、`agent-toolkit:atk-mode/references/review-record.md`「条件別判定行の形式」の返却前の確認（条件・要求単位と行の過不足、番号の欠落と重複）が成り立つことを確かめる。
4. 返却の`未解決の指摘数`と、各指摘管理表の`atk review-table show <表の絶対パス> --format=summary --round <そのレビューのround>`の`unanswered_count`と`recheck_pending_count`が0であることを確かめる。
5. `adopt`するWIの行が全て`達成`か、`agent-toolkit:atk-mode/references/review-record-verdicts.md`「判定の値」の表で受理できる区分名付きの行であることを、件数でなく各行の記録を読んで確かめる。
6. 各`達成`行が所在と観測内容の対を持ち、根拠が条件の指定した入力・操作・観測に合うことを確かめる。
   次の行は件数やHEADが一致していても受理せず、同じ実行レビュー担当へ再判定を求める。
   - 対が無い行と、条件文を繰り返して「確認した」などの定型句だけを変えた行。
   - 後続工程の予定か、指定された観測を別の隔離環境・部分実行で代えた記録だけで`達成`とした行。
7. 全体検証とCIの成功は`agent-toolkit:atk-mode/references/workflow-phases.md`の公開工程の判定で確かめる。
8. 受理できない行（欠落・重複・参照できない行を含む）が残れば、委譲元はWIと不足を実行レビュー担当へ差し戻し、レーン担当は不足するAWI・条件・要求単位を`続行できない理由:`に書いて報告する。
