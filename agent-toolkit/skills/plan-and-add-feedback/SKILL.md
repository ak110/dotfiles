---
name: plan-and-add-feedback
description: >
  計画作成からレビューまでを実施したうえで、
  実装の代わりにフィードバック投入で終える運用を実行するときに起動する。
  `/agent-toolkit:plan-and-add-feedback`による明示起動、または
  「計画してフィードバック投入で終えて」等の指示で起動する。
---

# 計画作成とフィードバック投入による終了

レビュー済み計画を後続の計画実装型処理へ引き継ぎ、起動元では計画作成と投入までを完了する。
本スキルの実行中は、計画ファイルの作成・改訂とフィードバック投入以外の変更を対象リポジトリへ加えない。
本スキルはplan mode外で実行する。

## 手順

1. `agent-toolkit:plan-mode`を起動し、実装委譲を除く調査、確認、計画作成、機械検査、レビューを完了する。
   本スキルはplan mode外で実行し、計画作成中に共有作業ツリーが変化した事実だけでは調査をやり直さない
2. 次の完成済み入力を`agent-toolkit:add-feedback`へ渡し、同スキルの非対話経路で投入する
   - 本文: `<計画ファイルの絶対パス> を実装する`
   - 対象リポジトリ: 計画の対象リポジトリ
   - plan file: 計画ファイルの絶対パス
   - source: `plan`
   - 先行feedbackとの依存関係: 計画作成時に確定した値
3. `agent-toolkit:add-feedback`が再取得したfilename、metadata、本文を利用者へ提示して終了する。
   `atk mq process-loop`の常駐環境では投入分が自動的に実装対象になること、
   意図と異なる場合は`atk mq rm <filename>`で取り消せることも示す

## 複数リポジトリ横断作業

作業が複数リポジトリを対象とする場合は、
`agent-toolkit/skills/process-feedbacks/references/plan-impl-feedback-flow.md`
「複数リポジトリ横断作業の分解投入」節に従って計画と完成済み本文を分ける。

## 動作モード

本スキル自体は協調モードで動作する。利用者の選好に依存する事項は計画確定前に確認し、
完成済み本文を`agent-toolkit:add-feedback`へ渡した後は問い直さない。
