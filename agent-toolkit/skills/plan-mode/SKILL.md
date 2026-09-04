---
name: plan-mode
description: >
  計画ファイルを作成して実装へ引き継ぐときに起動する。
  plan mode下、複数ファイル変更、多段階作業、バグ対応で起動する。
  バグ対応は単一ファイルの単純な修正でも起動対象とする。
  バグ対応を除く単一ファイルの単純な修正では起動しない。
---

# 計画モード

本スキルは、調査から計画ファイルの作成、計画レビュー及び起動経路別の終端までの工程制御を定める。
計画ファイルの成果物契約は`references/plan-file-standards.md`、各工程の内部手順は`${CLAUDE_PLUGIN_ROOT}/share/`配下のタスク文書を正本とし、本書へ再掲しない。

ユーザーが`agent-toolkit:plan-mode`又は`agent-toolkit:plan-and-add-awi`を直接起動した場合は、`references/grilling.md`に従いユーザーとの共通理解へ到達するまで確認を繰り返す。
起動プロンプトが起動経路として`agent-toolkit:process-wi`を明示している場合は`references/grilling.md`を使わず、必要な確認事項だけをUWIへ登録する。
既存の旧単一ファイル形式・旧二ファイル形式の計画を改訂するときだけ、`references/legacy-plan-file-standards.md`を全文読む。
計画書式の読み取り互換の実装・検査を変更するときも、同書を全文読む。

## 進め方

1. 適用規範、変更対象、定義・参照・呼び出し元、既存テスト、生成・配布経路、類似実装のうち、計画ファイルへ書く内容を確定するために必要な範囲を調査する
2. 計画の変更対象又は採用方針を左右する未確定判断を、判断同士の依存関係とともに列挙し、`agent-toolkit/rules/01-agent.md`「協調と自律」の確認要否判定を適用する。直接起動では`references/grilling.md`に従って確認を完了し、`agent-toolkit:process-wi`経路では確認事項をUWIへ登録する
3. `references/plan-file-standards.md`を全文読み、計画ファイル初版を起草する
4. 初版を起草した主体が`${CLAUDE_PLUGIN_ROOT}/share/plan-drafting.subagent.md`に従って計画構造検査と自己監査を完了する
5. 起動経路に対応する次の1行だけを実施して終端する

| 起動経路 | 手順4の後に実施すること | 終端 |
| --- | --- | --- |
| `agent-toolkit:plan-mode`の直接起動 | `${CLAUDE_PLUGIN_ROOT}/share/plan-review.parent.md`と`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`を読み、`plan_review_model`で計画レビュー担当を起動して各ラウンドを受領し、`計画レビュー完了`を検収する。起動から完了報告の受領までは計画ファイルを読み取り専用として扱う | 計画レビュー後も`~/.claude/plans`の実体を維持する。計画ファイル、成立させる結果、ユーザー指示との差分及びレビュー反映状況を提示し、ユーザー承認後に`${CLAUDE_PLUGIN_ROOT}/share/implementation.parent.md`を読んで`execute_fast_model`で実装担当を起動する。実装レビュー収束後の移動と保存は`${CLAUDE_PLUGIN_ROOT}/share/implementation-review.parent.md`の「実装レビュー後の計画最終化」を正本とする |
| `agent-toolkit:plan-and-add-awi`からの起動 | なし | 計画ファイル（メイン）・計画ファイル（詳細）の絶対パスを呼出元へ返す。計画レビューは呼出元が行い、実装引き継ぎは行わない |
| `${CLAUDE_PLUGIN_ROOT}/share/plan-drafting.subagent.md`を受領した計画担当としての起動 | なし | 同書の完了報告契約に従って呼出元へ返す。計画レビュー担当の起動判断と実装引き継ぎを行わない |

本スキルの起動後は、計画ファイルを作成するまで対象規範配下（`agent-toolkit/`等のコーディングエージェント向け規範文書）を直接編集しない（連続する直接編集は遮断される）。
