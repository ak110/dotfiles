---
name: review-standards
user-invocable: false
description: >
  コードレビュー・ドキュメントレビューを実施する時、
  レビュー指摘・改善提案・ユーザーの割り込みや是正要求・想定外の発見を受領して
  採否と修正を確定する時、および提案・自身の判断・サブエージェント出力の妥当性を
  評価する時に最初に必ず起動する。外部の記事や資料に基づく改善案の評価にも適用する。
  レビュー担当サブエージェント、レビューイーとなる計画担当・実装担当、
  およびメインエージェント自身のいずれもが起動する。
  著者向けの品質基準`agent-basics:writing-standards`と併用する。
# 編集時の注意点:
# レビュー担当側の基準はreferences/reviewer.md、レビューイー側の基準はreferences/reviewee.mdが定める。
# 両役割へ共通する契約だけを本体へ置く。
# レビュー工程管理と実行手段ごとの出力は各`<役割名>.subagent.md`が扱う。
---

# レビューの品質基準と運用

レビューと指摘の採否の品質基準は`agent-basics:review-standards`が定義する。本スキルはレビュー表、WIの証拠、委譲と工程管理を所有する。品質の定義は同スキルに集約する。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

<!-- read-table-source: SKILL-delivery-1.md -->

## 読込表

<!-- read-table-source: SKILL-delivery-2.md -->

| 時点または条件 | 取得する範囲 |
| --- | --- |
| 専用レビュータスク契約を受領し、判定と返却を行う前 | `references/reviewer.md`の「専用レビュータスク契約」「完了報告」 |
| 読者別探索を起動・検収する前 | `references/reviewer.md`の「読者別探索」 |
| 計画・WIの要件と証拠を判定する前 | `references/reviewer.md`の「計画と表の優先」 |
| レビューイーとして採否と修正を確定する前 | `references/reviewee.md` |
| 実行レビューの指摘登録・完成条件の判定と証拠記入の前 | `references/exec-review-recording.md` |

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 起動時 | `agent-basics:review-standards/SKILL.md` | 起動時 | `skill:review-standards` | |

## レビュー指摘管理表の共通操作

レビュー指摘管理表は、`atk review-table`が操作するヘッダーなしのTSVで、計画ファイルと同じstemの`.exec-review.tsv`を指す。CI対応レビュー指摘管理表は、対応する計画が無いCI失敗への対応をレビューした指摘を記録するレビュー指摘管理表（`ci-<OID>.exec-review.tsv`）を指す。
`atk review-table init`直後の0バイトの表は指摘0件の初期化済みの表であり、指摘なしで終端するレビューでもヘッダーや仮の指摘行を加えずそのまま使う。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/review-standards/SKILL.md：レビュー指摘管理表の共通操作：2026年10月8日」にある。
指摘の追加と応答の記録はいずれも`atk review-table`で行い、指摘箇所、指摘内容、対応内容および理由はファイルのパスを渡すオプションで渡す。役割ごとのサブコマンドと必須オプションは各参照資料が、列の形式と出力は`atk review-table --help`が定める。
`add`と`respond`は保存した本文を送信元と比べて検証するため、終了コード0なら保存本文の再取得と全文比較を省く。

## 対象成果物の作成規範

品質の定義は`agent-basics:review-standards`の`SKILL.md`「対象成果物の作成規範」を適用する。

## 文章成果物の実害

品質の定義は`agent-basics:review-standards`の`SKILL.md`「文章成果物の実害」を適用する。
