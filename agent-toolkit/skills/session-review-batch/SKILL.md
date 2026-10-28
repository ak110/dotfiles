---
name: session-review-batch
description: >
  OSアカウントに記録した振り返り待ちをまとめて分析する。
  SessionStart/SessionEndの自動起動、またはユーザーの手動起動で使う。
---

# 複数セッションの非同期振り返り

記録済みの非活動セッションをまとめて振り返り、共通する原因と対策を整理してAWIを登録する。
分析と参照資料は `agent-toolkit:session-review` を共用する。

## 名指し参照の読込方法

本文の条件に合う資料を全文読み、指定した契約を適用する。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../wi-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` |
| 対象の分析を始める前 | `../session-review/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/session-review/SKILL.md` |
| AWIを投入担当へ渡す前 | `${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md` | 判断 | `read:agent-toolkit:*/share/add-wi.parent.md` |
| 結果報告を保存する前 | `../completion-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/SKILL.md` |

## 起動

環境変数 `AGENT_TOOLKIT_SESSION_REVIEW_BATCH` が無い手動呼出しでは、次の公開操作を実行する。

```sh
atk run-skill --args manual agent-toolkit:session-review-batch
```

実際の分析はrun-skillが起動する別セッションで行う。
進行中の担当がある場合は、分析をその実行へ引き続き委ねる。
手動起動は自動停止設定と未回答の失敗UWIによる抑止を迂回するが、活動判定とアカウント共通排他は維持する。

## 分析と保存

1. `AGENT_TOOLKIT_SESSION_REVIEW_BATCH` が指すmanifest JSONを読む。
   `targets` が今回の固定対象、`prepared` が対象別の準備結果である。
   `target_repo` は今回1件の結果報告UWIの登録先であり、各対象の分析先と区別する。
   準備済みのbundleと文書を再利用し、対象集合と処理範囲をmanifestに固定して分析する。
2. `agent-toolkit:session-review` の分析基準・参照資料を読み、対象ごとの `conversation_path` を通読し、候補・統計・比較材料を評価する。
   各対象の `reference_document` があれば読む。
   同じ原因をセッション横断で整理し、対象IDと候補IDの組で出典を保持する。
   未確認の対象を成功とせず、既存の失敗対応に沿って原因と未完了範囲を報告する。
3. 原因と対策を確定したAWIを `agent-toolkit:wi-standards` と `${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md` の契約で登録する。
   sourceは `session-review` とし、投入担当へ、各AWIの保存直前の時刻から24時間後を `atk wi add --cooldown-until` へ指定するよう渡す。
   冷却期限の基準は各AWIの保存直前とする。
   所要時間目標と超過分析は対象1件あたり300秒として適用する。
4. `agent-toolkit:completion-report` の振り返り結果報告書式で、今回の結果を事後承認型UWIへ1件保存する。
   対象一覧、登録AWI・見送り・費用確認を対象IDと対応付ける。
   sourceは `run-skill`、scopeは `agent-toolkit:session-review-batch`、本文frontmatterは `run_skill_report: true` とし、manifestの `target_repo` を `--target-repo` へ明示する。
   同一実行の報告があれば同じUWIを更新して1件に保つ。
   再度completion-reportを実行する場合もこの報告を再利用する。
5. AWIと報告の保存を検収し、次の操作で分析の完了を記録する。

   ```sh
   atk run-script session-review-state -- finish
   ```

   分析完了の記録を保存した後、batchセッションを終了する。

`finish` は分析完了の記録だけを行う。
起動元run-skillが報告の同期を検収した後に、今回準備した範囲を処理済みにし、次の対象があれば次回を起動する。
失敗時はfinishを実行せず、原因と未完了対象を最終応答に残す。
待ち記録、失敗UWIの重複確認、未回答中の自動抑止は起動元が所有する。

## 状態定義

`_common/session_review.py`はhook・活動状態の削除から独立したOSアカウント共通の`{state_dir}/session-review/`を管理する。

- `pending/<host>-<session_id>.json`: 最上位SessionStartがhost・会話ID・記録パス・cwd・リポジトリ・開始更新時刻・登録世代を保存し、処理済み会話のUserPromptSubmitも再登録する。委譲先・サブエージェント・batch自身を除外し、成功確定か取消しまで保持する
- `reviewed/<host>-<session_id>.json`: prepareが増分を選ぶ記録別の最終完全行と同一性を保持し、旧結果が返っても確定範囲を維持する。元記録がすべて無く、pending・未解決失敗・未確定manifestから参照されない場合だけ回収する
- `runs/`: 実行ID別manifestに固定対象・報告先・準備範囲・分析結果・プロセス・失敗を保持し、中断後の報告同期と未確定範囲を検収する。成功確定済み、または報告同期済み失敗の全対象がpendingから除かれたmanifestとログはrun-skillと同じ30日保持とし、未確定・報告未達は確定まで保持する
- `state.lock`: モデル起動・利用上限待機・WI保存を除く、登録・状態更新・成功確定を短時間排他する
- `batch.lock`: 自動・手動の全リポジトリで対象選定から報告同期検収・成功確定まで共有する。親中断時もプロセス識別と子の起動引数に含めた固有manifestパスで生存を確認し、二重起動を防ぐ
- pendingとmanifestの失敗UWI対応により、同原因の未回答対象を自動選定から外し、手動再試行時も既存の報告を再利用する

batchはrun-skillの報告同期検収後、手動振り返りは全体分析後の`atk run-script session-review-state -- complete --prepared <準備結果JSON>`で準備済み範囲を確定し、新世代・準備後の追記はpendingに残す。
`AGENT_TOOLKIT_SESSION_REVIEW_BATCH`はmanifest、`AGENT_TOOLKIT_RUN_SKILL_TARGET_REPO`は報告先を子へ渡す。
`session_review_auto_start`がfalseでも待ちを登録し、SessionStartで件数と手動操作を通知する。
batch起動時の回収では、共有の起動診断`launch.log`も、pending・未確定manifestが無く最終更新から30日経過した場合だけ削除する。
