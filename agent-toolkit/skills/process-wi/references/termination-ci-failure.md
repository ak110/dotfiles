# 終端担当の検証またはCIの失敗

本書は、`${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`に基づく終端担当エージェントが、全体検証またはCIの失敗を受けた際に行う修正系列の管理、CI修正担当および実行レビューの起動、ならびにpushの更新手順を定める。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

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

計画を基準とする修正系列の実行レビューにおいて、取得した計画の進捗ログに記録されている受理済みの実行レビュー返却ファイルが後続担当から読み取り可能である場合は、その絶対パスを任意入力`受理済みの完成条件証拠`に指定する（存在しない場合は`なし`とする）。参照を使用する際の条件は、`agent-toolkit:review-standards`の`references/exec-review-recording.md`「受理済み判定への参照」に従う。

最初に観測された失敗からCIの成功、または本タスクの終端に至るまでを、1つの修正系列（`agent-toolkit:bugfix`の`references/ci-failure-handling.md`「修正系列」）として扱う。`agent-toolkit:bugfix`を起動し、ログ、参照実装、期待値から直接的な原因を特定した上で、`ci-failure-handling.md`で定められた項目を含むCI記録を保持する。

修正が必要と判断された場合は、`${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`に従い、メインの作業ツリーを対象作業ツリーとする`CI修正担当`を起動する。書き込みを行う主体はこのCI修正担当の1つのみに限定する。原因となったコミットに対応する保存済みの計画ファイルが存在する場合は、`ci-failure-handling.md`の`入力計画`の取得と、修正系列の終了時における再保存を終端担当が行う。取得した計画ファイルの`private-notes/plans/`からの相対パスと再保存の結果は、引き継ぎ記録に記録する。修正コミットと検証結果を受け取った後は、バージョン番号、manifest、生成ファイルの同期、push、CIの確認を改めて判定し直す。

`ci-failure-handling.md`「修正系列」の起動条件に合致する修正では、`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`に従って実行レビューを1件起動する。原因コミットに対応する計画が存在する場合は`レビュー基準: 計画`として計画ファイルを渡す。存在しない場合は`レビュー基準: CI記録`とし、所定の7項目に加えて`修正系列の開始時のHEAD`と`原因commitOID`を渡す。`修正系列の開始時のHEAD`は修正系列の開始時点のHEADを表す7文字以上の重複のない短縮OIDとし、系列の処理中は変更しない。`原因commitOID`は今回の原因コミットを表す7文字以上の重複のない短縮OIDとし、再帰的なCI失敗が発生した場合は7項目と同時に更新する。CI修正担当から受け取った`検証結果`は、同文書の冒頭の規定に従って引き継ぎ記録に記録する。同一の修正系列内では同一のレビュー指摘管理表を引き継いで使用する。`レビュー基準: 計画`の表は計画ファイルの再保存時にあわせて保存し、`レビュー基準: CI記録`の表は修正系列の終端処理時に`入力計画`の手順に従って終端担当が保存し、その結果を引き継ぎ記録に記録する。

修正系列の実施回数としてカウントするのは、是正した失敗をログの該当箇所および直接的な原因に対応付けられた修正のみとし、原因コミットへ統合（amendやfixup）した修正についても1回としてカウントする。ユーザーによる割り込み操作や、独立した事象に対するコミットはカウントに含めない。CI修正担当が修正を原因コミットへ統合した場合は、`agent-toolkit:commit`の`references/push-and-ci.md`「pushと監視」の「原因commitへ取り込む区分」に従う。期待値を明示した`--force-with-lease`を用いて同一ブランチを更新する。続いて、更新後のHEADと新しいベースラインを用いてCIの実行結果を再確認する。pushする対象範囲は変更せず、キューの操作はまだ`adopt`されていないAWIに対してのみ実施する。
