# 終端担当の検証またはCIの失敗

本書は`${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`の終端担当が、全体検証かCIの失敗を受けたときの修正系列の扱い、CI修正担当の起動、実行レビューの起動とpushの更新を定める。

## 検証またはCIの失敗

本書の手順から実行レビューを起動する場合の名前付き入力`未判定検証記録`には`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`の証拠要求の有無による値（証拠要求ありでは未判定検証記録の絶対パス、証拠要求なしでは`なし`）を渡す。

最初の失敗からCI成功または本タスクの終端までを1つの修正系列（`agent-toolkit:bugfix`の`references/ci-failure-handling.md`）として扱う。`agent-toolkit:bugfix`を起動してログの該当箇所、参照実装および期待値から直接的原因を確定し、`agent-toolkit:bugfix`の`references/ci-failure-handling.md`が定める項目を持つCI記録を保持する。

修正が必要な場合は`${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`に従い、主作業ツリーを対象worktreeとする`CI修正担当`を起動する。原因commitに対応する計画が保存済みの場合は、`agent-toolkit:bugfix`の`references/ci-failure-handling.md`が定める`入力計画`の取得と修正系列の終端での再保存は終端担当が行う。取得した計画の`private-notes/plans/`からの相対パスと再保存の結果は引き継ぎ記録へ残す。同じworktreeへ別の書込主体を並存させず、書込主体はこの修正担当1つとする。CI修正担当から修正commitと検証結果を受領し、版数、manifest、生成同期、pushおよびCI確認を再判定する。

同一の修正系列における3回目以降の修正と、回数によらず公開契約または設計へ及ぶ修正では`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`に従って実行レビューを1件起動する。原因commitに対応する計画があれば`レビュー基準: 計画`、無ければ`レビュー基準: CI記録`とする。計画では計画ファイルを渡す。CI記録では7項目に加えて`修正系列の開始時のHEAD`と`原因commitOID`を渡す。`修正系列の開始時のHEAD`はその修正系列を始めた時点のHEADの7文字以上の一意な短縮OIDとし同じ修正系列で継続する。`原因commitOID`は今回の原因commitの7文字以上の一意な短縮OIDとし、再帰的CI失敗では7項目と同時に更新する。同じ修正系列では同じレビュー指摘管理表を継続する。`レビュー基準: 計画`の表は計画の再保存で計画と共に保存される。`レビュー基準: CI記録`の表は計画の再保存と同じく`agent-toolkit:bugfix`の`references/ci-failure-handling.md`の`入力計画`の段落に従って修正系列の終端で終端担当が保存し、保存の結果を引き継ぎ記録へ残す。

各修正が是正した失敗を、ログの該当箇所と直接的原因へ対応付ける。修正系列の回数へ数えるのは、この対応付けができた修正に限り、原因commitへ取り込んだ修正も1回として数える。ユーザー割り込みまたは独立事象へのcommitは修正系列の回数へ含めない。CI修正担当が修正を原因commitへ取り込んだ場合は、同じbranchを`agent-toolkit:commit`の`references/push-and-ci.md`「pushと監視」手順5の期待値を明示した`--force-with-lease`で更新する。続けて、書き換え後の対象HEADと新しいbaselineでCIを再確認する。本タスクがpushする範囲はこれまでどおりとする。キュー操作は、まだ`adopt`していないAWIだけへ行う。
