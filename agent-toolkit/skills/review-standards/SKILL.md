---
name: review-standards
user-invocable: false
description: >
  コードレビューやドキュメントレビューを実施するとき、
  レビュー指摘・改善提案・ユーザーからの割り込みや修正要求・想定外の発見を受け取って
  採否と修正内容を確定するとき、および提案・自身の判断・サブエージェントの出力の妥当性を
  評価するときに最初に必ず起動する。外部の記事や資料に基づく改善案の評価にも適用する。
  レビュー担当サブエージェント、レビューイーとなる計画担当・実装担当、
  およびメインエージェント自身のいずれもが起動する。
  執筆者向けの品質基準`agent-basics:writing-standards`と併用する。
# 編集時の注意点:
# レビュー担当側の基準はreferences/reviewer.md、レビューイー側の基準はreferences/reviewee.mdが定める。
# 両役割へ共通する契約だけを本体へ置く。
# レビュー工程管理と実行手段ごとの出力は各`<役割名>.subagent.md`が扱う。
---

# レビューの品質基準と運用

レビューおよび指摘の採否に関する品質基準は、`agent-basics:review-standards`で定義している。本スキルでは、レビュー指摘管理表の操作、WIの完成条件証拠、委譲と工程管理を扱う。品質の定義そのものは同スキルに集約する。

## 名指し参照の読込方法

本文に記載された各参照の適用条件に従い、選んだ資料の指定範囲を読み込んでから、その基準に基づく具体的な判断へ進む。起動時に読む資料は、起動時のプロンプトとともに提供される。

<!-- read-table-source: SKILL-delivery-1.md -->

## 読込表

<!-- read-table-source: SKILL-delivery-2.md -->

| 時点または条件 | 取得する範囲 |
| --- | --- |
| 専用レビュータスク契約を受領し、判定と返却する前 | `references/reviewer.md`の「専用レビュータスク契約」「完了報告」 |
| 読者別探索を起動・検収する前 | `references/reviewer.md`の「読者別探索」 |
| 計画・WIの要件と証拠を判定する前 | `references/reviewer.md`の「計画と表の優先」 |
| レビューイーとして採否と修正を確定する前 | `references/reviewee.md` |
| 実行レビューの指摘登録・完成条件の判定と証拠記入の前 | `references/exec-review-recording.md` |

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 起動時 | `agent-basics:review-standards/SKILL.md` | 起動時 | `skill:review-standards` | |

## レビュー指摘管理表の共通操作

レビュー指摘管理表は、`atk review-table`が操作するヘッダーなしのTSVファイルであり、計画ファイルと同じベース名を持つ`.exec-review.tsv`を指す。CI対応レビュー指摘管理表は、対応する計画ファイルが存在しないCI失敗への対応において、レビュー指摘を記録する表（`ci-<OID>.exec-review.tsv`）を指す。
`atk review-table init`を実行した直後の0バイトのファイルは、指摘が0件の初期化済みテーブルである。指摘がないままレビューを完了する場合も、ヘッダー行やダミーの指摘行を追加せず、そのまま使用する。これに関する監査記録は、`docs/development/audit-records.md`の「agent-toolkit/skills/review-standards/SKILL.md：レビュー指摘管理表の共通操作：2026年10月8日」に記録されている。
指摘の追加や応答の記録は、すべて`atk review-table`コマンドで行う。指摘箇所、指摘内容、対応内容、および対応不要の理由は、ファイルパスを受け取るオプション経由で渡す。役割ごとのサブコマンドや必須オプションは各参照資料を参照し、列のフォーマットや出力形式は`atk review-table --help`で確認する。
`add`や`respond`は、保存された本文と送信元データを自動的に突き合わせて検証する。そのため、コマンドが終了コード0で正常終了した場合は、保存された本文を再取得して全文比較する必要はない。

## 対象成果物の作成規範

品質の定義については、`agent-basics:review-standards`の`SKILL.md`「対象成果物の作成規範」に従う。

## 文章成果物の実害

品質の定義については、`agent-basics:review-standards`の`SKILL.md`「文章成果物の実害」に従う。
