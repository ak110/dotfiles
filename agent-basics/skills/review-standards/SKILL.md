---
name: review-standards
user-invocable: false
description: >
  コードレビューやドキュメントレビューを実施するとき、
  レビュー指摘・改善提案・ユーザーからの割り込みや是正要求・作業中の想定外の発見を受け取って
  採否と修正内容を確定するとき、および提案や自身の判断、サブエージェントの出力の妥当性を
  評価するときに最初に必ず起動する。外部の記事や資料に基づく改善案を評価するときにも適用する。
  レビュー担当のサブエージェント、レビューイーとなる計画担当や実装担当、
  およびメインエージェント自身のいずれも起動する。
  執筆者向けスキル`agent-basics:writing-standards`と併用する。
# 編集時の注意点:
# レビュー担当側の基準はreferences/reviewer.md、レビューイー側の基準はreferences/reviewee.mdが定める。
# 両役割へ共通する契約だけを本体へ置く。
# レビュー工程管理と実行手段ごとの出力は各委譲先の手順書が扱う。
---

# レビュー担当とレビューイーの心得

本スキルは、レビューを実施するエージェントと、指摘を受け取って採否や修正内容を確定するエージェントに向けて判断基準を提供する。
指摘の確定基準、種類、範囲、書き方は、本スキルと`references/reviewer.md`で定める。レビュー用の役割文書やスキルで独自の判定観点を追加することは避け、不足があれば本スキルを改訂して補う。観点が分散すると同じ成果物に対する判定が分かれ、修正と再レビューが収束しにくくなるためである。
本スキルの「実行レビュー」は、実装後に変更差分を独立した文脈で確かめるレビューを指す。
作業時はまず自身の役割を確定し、該当する役割の資料を読み込む。同じエージェントが後から別の役割を担う場合は、その時点で改めて役割を確定する。

レビューで適切に判断するには、対象の成果物と契約内容を読み取る必要がある。また、Gitの実体確認や検証、観測結果を保存するには、それぞれの操作手段も欠かせない。資料の読み取りだけが可能な環境では、提示された情報で裏付けを取れた指摘と、追加の観測が必要な範囲を返す。検証や保存を伴う実行レビューは、それらの操作を実行できる環境で進める。指摘文の表現は[文章の品質基準](references/quality-standards.md)に沿って確認する。

QCD、3段判定、事実と推論、硬い制約と努力目標の定義は、`agent-basics:writing-standards`の`references/quality-principles.md`に従う。

## 名指し参照の読込方法

本文で名指しした資料と読込表の資料は、条件が成立したらその判断や操作の前に自分で全文読み、指定箇所に注目してから、その基準で判断する。読込表の列の使い方は`agent-basics:writing-standards`の`references/quality-principles.md`「読込表の読み方」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../writing-standards/SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `references/quality-standards.md` | 判断 | `read:agent-basics:*/skills/review-standards/references/quality-standards.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../writing-standards/references/design-heuristics.md#「設計案の予備選別」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/design-heuristics.md#「設計案の予備選別」` |
| 初回レビューで対象成果物の作成規範を適用する時 | `../writing-standards/references/review-only-criteria.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/review-only-criteria.md` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| レビューを実施するエージェント（レビュー担当）として判定する前 | `references/reviewer.md`の「適用契約」「基本方針」「指摘内容」と、初回は「初回レビュー」、再レビューは「再レビューの場合」 | 判断 | `section:agent-basics:*/skills/review-standards/references/reviewer.md#「適用契約」「基本方針」「指摘内容」` | |
| レビュー担当が要求単位・完成条件と観測の達成を判定する前 | `references/reviewer.md`の「要求単位と観測の判定」 | 判断 | `section:agent-basics:*/skills/review-standards/references/reviewer.md#「要求単位と観測の判定」` | |
| 実行レビューを判定する前 | `references/reviewer.md`の「実行レビューの判定」 | 判断 | `section:agent-basics:*/skills/review-standards/references/reviewer.md#「実行レビューの判定」` | |
| 画面のユーザビリティを判定する前 | `references/reviewer.md`の「ユーザビリティレビューの判定」 | 判断 | `section:agent-basics:*/skills/review-standards/references/reviewer.md#「ユーザビリティレビューの判定」` | |
| 是正要求等（レビュー指摘、改善提案、ユーザーの割り込みや是正要求、作業中の想定外の発見）を受領し、採否と修正を確定するエージェント（レビューイー）として判定する前 | `references/reviewee.md` | 判断 | `read:agent-basics:*/skills/review-standards/references/reviewee.md` | |
| 役割を問わず、計画・設計の採用案や是正要求等の妥当性を評価する前 | `references/judgment-details.md`の「問題と手段の比例性」「解決案の比較」「判断の点検」「規定の適用可否」「前提の再検討」 | 判断 | `section:agent-basics:*/skills/review-standards/references/judgment-details.md#「問題と手段の比例性」「解決案の比較」「判断の点検」「規定の適用可否」「前提の再検討」` | |
| 既存記述との重複・方針との整合を判定する前 | `references/judgment-details.md`の「既存記述との重複と方針との整合」 | 判断 | `section:agent-basics:*/skills/review-standards/references/judgment-details.md#「既存記述との重複と方針との整合」` | |
| 検収手段の到達可能性を判定する前 | `references/judgment-details.md`の「検収手段の到達可能性」 | 判断 | `section:agent-basics:*/skills/review-standards/references/judgment-details.md#「検収手段の到達可能性」` | |
| 矛盾の原因を調べる前 | `references/judgment-details.md`の「矛盾の原因調査」 | 判断 | `section:agent-basics:*/skills/review-standards/references/judgment-details.md#「矛盾の原因調査」` | |
| 要求ごとの観測と判定根拠を記録・再利用する前 | `references/exec-review-recording.md` | 判断 | `read:agent-basics:*/skills/review-standards/references/exec-review-recording.md` | |
| 起動時 | `agent-basics:writing-standards/references/quality-principles.md` | 起動時 | `skill:review-standards` | |
| いずれの役割でも、判定を始める前 | 「対象成果物の作成規範」が指す作成規範 | 判断 | `skill:review-standards` | `artifact-standards` |

どちらの役割でも、判定は`agent-basics:writing-standards`の`references/quality-principles.md`にある3段判定（許容性、必要性、実装品質の順）に沿って行う。案の必要性、既存機構の活用、過剰設計の回避、局所修正と共通原因修正の選択、責務の割り振りの比較は、`agent-basics:writing-standards`の`references/design-heuristics.md`「設計案の予備選別」に従う。
レビュー担当とレビューイーの間で元の要求の解釈が分かれ、成果物や操作に違いが出る場合は、原文、これまでの発話、実際の回答、現物の成果物を突き合わせて差異を解消する。
判断材料としてユーザー固有の意図や情報がまだ必要な場合は、双方の解釈とそれぞれの結果をまとめ、ユーザーへ確認できるメインエージェントに返す。
実際の回答が得られるまでは、その解釈を前提とする指摘の採否と修正を保留し、影響のない独立した作業を進める。

## 対象成果物の作成規範

作成規範とは、対象成果物の種類ごとに適用する作成時の品質基準である。`agent-basics:writing-standards`の該当資料に加え、対象リポジトリに実在する`AGENTS.md`、`CLAUDE.md`、`.claude/rules/`配下の文書を指す。
レビュー担当は初回レビューにおいて、作成規範と合わせて`agent-basics:writing-standards`の`references/review-only-criteria.md`を一括して適用する。
レビューイーは、指摘で言及された基準のみを同資料から読み取る。

## 文章成果物の実害

文章成果物における実害とは、読者が本文から行為者、対象、条件、結果を一意に読み取れず、判断、操作、検証を誤るか作業を進められなくなる状態を指す。
文章に対する指摘の採否は、本文が作成規範やレビュー限定基準の条文に違反しているか、または前述の実害が生じるかで判定する。
機能面での問題の有無や計画や作業項目の完了条件を満たしているかは、文章の採否とは独立して確認する。
エンドユーザー向け文書の誤記や、適用対象となっているスタイルへの違反は、品質（Q）に影響する指摘として修正する。
