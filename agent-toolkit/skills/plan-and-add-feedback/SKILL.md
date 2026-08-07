---
name: plan-and-add-feedback
description: >
  計画作成からレビューまでを実施したうえで、実装の代わりにフィードバック投入で終える運用を実行するときに起動する。
  `/agent-toolkit:plan-and-add-feedback`又は「計画してフィードバック投入で終えて」等の指示で起動する。
---

# 計画作成とフィードバック投入による終了

レビュー済み計画を後続処理へ引き継ぐ。本スキルはplan mode外で実行する。計画ファイルの作成・改訂と
feedback投入以外の変更を対象リポジトリへ加えない。

## 手順

1. 計画作成前に`${CLAUDE_PLUGIN_ROOT}/skills/add-feedback/references/coordination-preflight.md`を全文読み、
   active、processing、関連worktreeを確認する。複数repoの場合だけ
   `${CLAUDE_PLUGIN_ROOT}/skills/add-feedback/references/cross-repository-submission.md`も全文読む
2. 重複inboxを計画へ吸収すると確定した直後に、実行主体が`agent-toolkit:add-feedback`をSkill機能で起動し、
   対象worktree、filename、理由、leaseを渡して予約する。予約より先に追加調査、計画起草、reviewへ進まない。
   失敗時はactiveを再取得して判断をやり直す
3. 計画に使うworktreeの絶対パスとbase commit、返されたreservation tokenとfilenameを保持する
4. 実行主体が`agent-toolkit:plan-mode`をSkill機能で起動し、対象worktreeと調査済み事実を渡す。
   実装委譲を除く調査、確認、計画作成、機械検査、reviewを完了する
5. 調査完了、起草開始、review開始、指摘反映開始の各境界で期限を確認し、工程中に期限へ達し得る場合は
   `renew-reservation`を実行する。期限切れなら所有権を仮定せずactiveから再判定する
6. 完成後、実行主体が`agent-toolkit:add-feedback`をSkill機能で起動し、本文、対象worktreeの絶対パス、
   base commit、plan file、source `plan`、依存、reservation tokenとfilenameを渡す。
   add-feedbackは保存直前に最新状態を再確認し、`merge-inbox`による統合、重複終端、予約解除を一括実行する
7. 保存後のfilename、metadata、本文を利用者へ提示する

計画を投入せず終了する場合や継続不能時は、同一セッション内で`release-reservation`を実行してinboxへ戻す。
異常終了で残った予約はprocess-feedbacksが期限切れ又は不正予約として検知し、所有工程を確認して回収する。

本スキルは協調モードで動作する。利用者の選好は計画確定前に確認し、完成済み本文を
add-feedbackへ渡した後は問い直さない。
