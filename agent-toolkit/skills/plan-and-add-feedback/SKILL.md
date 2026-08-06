---
name: plan-and-add-feedback
description: >
  計画作成からレビューまでを実施したうえで
  実装の代わりにフィードバック投入で終える運用を実行するときに起動する。
  `/agent-toolkit:plan-and-add-feedback`によるユーザー明示起動、または
  「計画してフィードバック投入で終えて」等の指示で起動する。
# 編集時の注意点:
# 本スキルは`agent-toolkit:plan-mode`「進め方」節の`plan-impl-executor`起動工程の代替として
# `agent-toolkit:process-feedbacks`「フィードバック投入」節を呼ぶ。`plan-mode`・
# `process-feedbacks`のロジックは複製せず参照呼び出しに徹する。
# 「複数リポジトリ横断作業の分解投入」節は
# `agent-toolkit/skills/process-feedbacks/references/plan-impl-feedback-flow.md`
# 「複数リポジトリ横断作業の分解投入」節をSSOTとする参照節であり、
# `.chezmoi-source/dot_claude/skills/sync-cross-project/SKILL.md`の同名節と意図的重複する
# （改訂時は3ファイルの整合を取ること）。
---

# 計画作成とフィードバック投入による終了

レビュー済みの計画を後続の計画実装型処理へ引き継ぎ、起動元のセッションでは計画作成とフィードバック投入までを完了する。

本スキルの実行中は、計画ファイルの作成・改訂とフィードバックの投入以外の変更を対象リポジトリへ加えない。
作業中に発見した既存不良・陳腐化した記述は、規模基準によらず`atk mq add`で登録し、当該セッションでは
是正しない（`agent-toolkit/rules/01-agent.md`「完遂と先送り」節が同一セッション内での対応を求める事象も
登録へ回す）。自セッションで導入した不良（計画ファイル自体の不備など）の是正は同一セッション内で完遂する。
本スキルは常駐のフィードバック処理ループと並列で起動されることが多く、双方が同一リポジトリへコミットすると
実装差分の境界と検証対象が混ざるためである。

## 手順

1. `agent-toolkit:plan-mode`スキルを参照呼び出しする。「進め方」節のうち
   `plan-impl-executor`起動を除く全工程（調査・確認・計画ファイルの作成と整合性チェック・codexレビュー）を完遂する。
   計画確定時は同節の工程5が定める全提示をユーザーへ行う。
   本スキルはplan mode外で実行する。メイン側で`EnterPlanMode`を発行しない。
   既にplan mode下で起動された場合は、本スキルをplan mode外で実行する旨を`ExitPlanMode`で提示し、
   承認を得てから工程1へ進む
   計画レビュー中に共有作業ツリーが変化しても、その事実だけでは調査やレビューをやり直さず、
   利用者向けの進捗へ表示しない
2. `plan-impl-executor`起動工程を実施しない。代わりに`agent-toolkit:process-feedbacks`「フィードバック投入」節の手順を実行する。
   「`<計画ファイルの絶対パス>` を実装する」という本文を対象リポジトリへ投入する
   （対象リポジトリの判別は同節の規定に従う）。
   投入時は`--plan-file=<計画ファイルの絶対パス>`と`--source=plan`を指定する。
   前者が当該フィードバックを計画実装型として確定し、後者が投入元を計画作成経由として記録する。
   投入したフィードバックは`agent-toolkit:process-feedbacks`「ステップ1: 入力の確定と初期スケジューリング」で
   計画実装型として扱われ、計画作成を経ずに実装される。
   実装担当は着手時に現行実装を再読し、利用者向け成果を変えない範囲で計画の実装手順を調整する
3. フィードバック投入結果をユーザーへ提示して終了する。`agent-toolkit:exit-session`は
   呼ばずセッションを継続する
   - 提示本文へ、`atk mq process-loop`常駐環境では投入分が自動的に実装開始される旨を予告する。
     意図と異なる場合の取り消し手段（`atk mq rm <投入ファイル名>`）と、
     内容を修正する非対話手段
     （`atk mq edit <投入ファイル名> $'<修正後の計画実装指示>'`）も対象ファイル名付きで併記する
   - 本規定の適用範囲は本スキル自身の手順内に限定する
   - 後続でStopフック起点の振り返りスキル（`agent-toolkit:session-review`・
     `session-review-dotfiles`等）が起動された場合は当該スキルの終了手順
     （`agent-toolkit:exit-session`遷移を含む）に従う

## 複数リポジトリ横断作業の分解投入

作業が複数リポジトリを対象とする場合に適用する。分解投入運用のSSOTは
`agent-toolkit/skills/process-feedbacks/references/plan-impl-feedback-flow.md`
「複数リポジトリ横断作業の分解投入」節とする。

## 想定動作モード

`agent-toolkit/rules/01-agent.md`「協調と自律」節の判定基準上、本スキルの起動自体は
`process_feedbacks_skill_invoked`を伴わないため協調モードで動作する。`AskUserQuestion`での
確認を基本とし、応答が得られない場合は`atk mq add --type=tbd`で記録して暫定判断のまま続行する。
