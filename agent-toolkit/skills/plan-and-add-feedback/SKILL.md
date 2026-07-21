---
name: plan-and-add-feedback
description: >
  計画作成からレビューまでを実施したうえで
  実装の代わりにフィードバック投入で終える運用を実行するときに起動する。
  `/agent-toolkit:plan-and-add-feedback`によるユーザー明示起動、または
  「計画してフィードバック投入で終えて」等の指示で起動する。
# 編集時の注意点:
# 本スキルは`agent-toolkit:plan-mode`工程7（plan-impl-executor起動）の代替として
# `agent-toolkit:add-feedback`を呼ぶ。`plan-mode`・`add-feedback`のロジックは複製せず
# 参照呼び出しに徹する。
# 「複数リポジトリ横断作業の分解投入」節は
# `agent-toolkit/skills/process-feedbacks/references/plan-impl-feedback-flow.md`
# 「複数リポジトリ横断作業の分解投入」節をSSOTとする参照節であり、
# `.chezmoi-source/dot_claude/skills/sync-cross-project/SKILL.md`の同名節と意図的重複する
# （改訂時は3ファイルの整合を取ること）。
---

# 計画作成とフィードバック投入による終了

## 前提

`atk fb status`が正常終了する環境でのみ動作する。非正常終了の場合は標準エラー出力を
ユーザーへ提示して終了する。

## 手順

1. `agent-toolkit:plan-mode`スキルを参照呼び出しする。工程1〜6（要件対話・調査・認識合わせ・
   恒久化検討・リファクタリング検討・計画ファイルの作成と整合性チェック・codexレビュー）を完遂する。
   本スキルはplan mode外で実行する。メイン側で`EnterPlanMode`を発行しない
   （PreToolUseフックが`plan_and_add_feedback_skill_invoked`真時にブロックする）。
   既にplan mode下で起動された場合は、本スキルをplan mode外で実行する旨を`ExitPlanMode`で提示し、
   承認を得てから工程1へ進む
2. 工程7（`plan-impl-executor`起動）を実施しない。代わりに`agent-toolkit:add-feedback`スキルを呼び出す。
   「`<計画ファイルの絶対パス>` を実装する」という本文を対象リポジトリへ投入する
   （対象リポジトリの判別は`add-feedback`ステップ2の規定に従う）。
   投入したフィードバックは`agent-toolkit:process-feedbacks`「ステップ1: 入力の確定」の分類で
   計画実装型として扱われ、計画作成を経ずに実装される
3. フィードバック投入結果をユーザーへ提示して終了する。`agent-toolkit:exit-session`は
   呼ばずセッションを継続する
   - 提示本文へ、`atk fb process-loop`常駐環境では投入分が自動的に実装開始される旨を予告する。
     意図と異なる場合の取り消し手段（`atk fb rm <投入ファイル名>`・`atk fb edit <投入ファイル名>`）も
     対象ファイル名付きで併記する
   - 本規定の適用範囲は本スキル自身の手順内に限定する
   - 後続でStopフック起点の振り返りスキル（`agent-toolkit:session-review`・
     `session-review-dotfiles`等）が起動された場合は当該スキルの終了手順
     （`agent-toolkit:exit-session`遷移を含む）に従う

## 複数リポジトリ横断作業の分解投入

作業が複数リポジトリを対象とする場合に適用する。分解投入運用のSSOTは
`agent-toolkit/skills/process-feedbacks/references/plan-impl-feedback-flow.md`
「複数リポジトリ横断作業の分解投入」節とする。

## 想定動作モード

`agent-toolkit/rules/02-collaboration.md`の判定基準上、`acceptEdits`等の協調モードでの
利用を主眼とし、ユーザー確認は通常の確認規範に従う。
自律実行モード下で起動された場合は同ファイル「自律実行モード」節を次の前提で適用する。

- 処理対象: 起動時のユーザー指示1件（計画ファイル1件の作成とフィードバック投入の完了を完了判定基準とする）
- ユーザー確認事項の記録先: `atk tb add`
  （本スキルは`atk fb status`の正常終了を前提とするため`atk`不在分岐はない）
- 追加停止契機: なし（同節既定の前提崩れのみで停止する）
