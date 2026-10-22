# 終端担当の検証またはCIの失敗

本書は`${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`の終端担当が、全体検証かCIの失敗を受けたときの修正系列、CI修正担当と実行レビューの起動、pushの更新を定める。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/session-termination.subagent.md` | 判断 | `read:agent-toolkit:*/share/session-termination.subagent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../review-standards/references/exec-review-recording.md#「受理済み判定への参照」` | 判断 | `section:agent-toolkit:*/skills/review-standards/references/exec-review-recording.md#「受理済み判定への参照」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/references/ci-failure-handling.md#「修正系列」` | 判断 | `section:agent-toolkit:*/skills/bugfix/references/ci-failure-handling.md#「修正系列」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../bugfix/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/exec.parent.md` | 判断 | `read:agent-toolkit:*/share/exec.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/exec-review.parent.md` | 判断 | `read:agent-toolkit:*/share/exec-review.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../commit/references/push-and-ci.md#「pushと監視」` | 判断 | `section:agent-toolkit:*/skills/commit/references/push-and-ci.md#「pushと監視」` |

## 検証またはCIの失敗

計画を基準とする修正系列の実行レビューでは、取得した計画の進捗ログに記録された受理済み証拠が後続担当から読める場合、その絶対パスを任意入力`受理済みの完成条件証拠`へ渡す（無ければ`なし`）。未判定検証記録とは別の入力であり、参照を使う条件は`agent-toolkit:review-standards`の`references/exec-review-recording.md`「受理済み判定への参照」に従う。

最初の失敗からCI成功または本タスクの終端までを1つの修正系列（`agent-toolkit:bugfix`の`references/ci-failure-handling.md`「修正系列」）として扱う。`agent-toolkit:bugfix`を起動してログ、参照実装、期待値から直接的原因を確定し、`ci-failure-handling.md`が定める項目を持つCI記録を保持する。

修正が必要なら`${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`に従い、主作業ツリーを対象worktreeとする`CI修正担当`を起動する。書込主体はこの修正担当1つとする。原因commitに対応する保存済みの計画がある場合は、`ci-failure-handling.md`の`入力計画`の取得と修正系列の終端での再保存を終端担当が行い、取得した計画の`private-notes/plans/`からの相対パスと再保存の結果を引き継ぎ記録へ残す。修正commitと検証結果を受領したら、版数、manifest、生成同期、push、CI確認を判定し直す。

`ci-failure-handling.md`「修正系列」の起動条件に当たる修正では、`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`に従って実行レビューを1件起動する。原因commitに対応する計画があれば`レビュー基準: 計画`で計画ファイルを渡す。無ければ`レビュー基準: CI記録`とし、7項目に加えて`修正系列の開始時のHEAD`（修正系列の開始時点のHEADの7文字以上の一意な短縮OID。系列の間は変えない）と`原因commitOID`（今回の原因commitの7文字以上の一意な短縮OID。再帰的なCI失敗では7項目と同時に更新する）を渡す。名前付き入力`未判定検証記録`は同書冒頭の規定に従って渡す。同じ修正系列では同じレビュー指摘管理表を続け、`レビュー基準: 計画`の表は計画の再保存とともに、`レビュー基準: CI記録`の表は修正系列の終端で`入力計画`の段落に従って終端担当が保存し、結果を引き継ぎ記録へ残す。

修正系列の回数に数えるのは、是正した失敗をログの該当箇所と直接的原因へ対応付けられた修正だけとし、原因commitへ取り込んだ修正も1回と数える。ユーザー割り込みや独立事象へのcommitは数えない。CI修正担当が修正を原因commitへ取り込んだ場合は、`agent-toolkit:commit`の`references/push-and-ci.md`「pushと監視」の「原因commitへ取り込む区分」が定める、期待値を明示した`--force-with-lease`で同じbranchを更新する。続けて書き換え後のHEADと新しいbaselineでCIを確認し直す。pushする範囲は変えず、キュー操作はまだ`adopt`していないAWIだけへ行う。
