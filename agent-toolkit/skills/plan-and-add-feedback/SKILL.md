---
name: plan-and-add-feedback
description: >
  計画作成からレビューまでを実施したうえで、実装の代わりにフィードバック投入で終える運用を実行するときに起動する。
  `/agent-toolkit:plan-and-add-feedback`又は「計画してフィードバック投入で終えて」等の指示で起動する。
---

# 計画作成とフィードバック投入による終了

本スキルは、レビュー済み計画を後続処理へ引き継ぐ手順を提供する。本スキルはplan mode外で実行する。計画ファイルの作成・改訂と
feedback投入以外の変更を対象リポジトリへ加えない。

## 手順

1. 計画作成前に現行plugin rootを確定する。Claude Codeでは`${CLAUDE_PLUGIN_ROOT}`を使い、Codexでは
   読み込んだ本`SKILL.md`の絶対パスから親ディレクトリを2階層たどる。
   `<plugin root>/skills/add-feedback/references/coordination-preflight.md`を全文読み、active、processing、
   関連worktreeを確認する。複数repoの場合だけ
   `<plugin root>/skills/add-feedback/references/cross-repository-submission.md`も全文読む
2. 同じ対象リポジトリに回答済みTBDがある場合は、重複inboxの終端より先に処理を完了する
3. 重複inboxを計画へ吸収すると確定した直後に、
   `atk mq reject <filename> --if-inbox --note=<移管理由と計画パス>`を実行する。
   追加調査、計画起草、reviewより前に実行し、rejectedへ移したfilenameと本文を保持する。
   状態競合で拒否された場合はactiveを再取得し、processing項目を変更しない
4. 計画に使うworktreeの絶対パスとbase commitを保持する
5. 実行主体が`agent-toolkit:plan-mode`をSkill機能で起動し、対象worktreeと調査済み事実を渡す。
   実装委譲を除く調査、確認、計画作成、機械検査、reviewを完了する
6. 完成後、実行主体が`agent-toolkit:add-feedback`をSkill機能で起動し、本文、対象worktreeの絶対パス、
   base commit、plan file、source `plan`、依存、吸収元filenameを渡す。
   add-feedbackは保存直前に最新状態を再確認し、新しい計画feedbackを追加する
7. 保存後のfilename、metadata、本文、吸収元filenameを利用者へ提示する

計画を投入せず終了する場合や継続不能時は、rejectedへ保存した本文を入力として
`agent-toolkit:add-feedback`をSkill機能で起動し、同一セッション内で再投入する。

本スキルは協調モードで動作する。利用者の選好は計画確定前に確認し、完成済み本文を
add-feedbackへ渡した後は問い直さない。
