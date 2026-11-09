# WIの調査・起草・保存の共通手順：直接起草と独立した検収の選択

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| セッション記録を復元・進捗・操作の観測へ使う前 | `../../review-session/references/session-records-conversation.md` | 判断 | `read:agent-toolkit:*/skills/review-session/references/session-records-conversation.md` |
| 依頼の`再発の経過を問う要求`が`該当`の場合に加え、起草者が逐語本文・趣旨から失敗・誤り・指摘の反復、頻度、悪化、いつからか守られなくなったことへの言及と判断した要求で、原因を確定する前 | `agent-basics:bugfix/references/root-cause-severity.md`と`agent-basics:bugfix/references/root-cause-scenarios.md`を全文読む | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity.md`、`read:agent-basics:*/skills/bugfix/references/root-cause-scenarios.md` |
| 原因分析の書式を使う前 | `agent-toolkit:wi-standards/references/analysis-records.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/analysis-records.md` |
| 原因分析の処置を記録する前 | `agent-basics:bugfix/references/root-cause-actions-hierarchy.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-actions-hierarchy.md` |
| 報告用UWIの確認・保存手順へ切り替える前 | `../../report-completion/references/report-formats-uwi.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/references/report-formats-uwi.md` |
| 複数リポジトリの共通本文を参照するAWIを読む前 | `cross-repository-submission.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/cross-repository-submission.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `draft-semantic-review.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/draft-semantic-review.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../add-awi-by-user/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/add-awi-by-user/SKILL.md` |
| 保存する本文の共通品質を判定する前 | `agent-toolkit:delegation/references/writing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/writing.md` |
| エージェント向け文書を追加・変更するAWIを起草する前 | `agent-basics:writing-standards/references/agent-documents-additions.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-additions.md` |

## 直接起草と独立した検収の選択

1. WI作成専用セッションでは、メインエージェント自身が調査・起草・保存することを原則とする。
   `agent-toolkit:add-awi-by-user`では、以下の4条件によらず毎回投入前レビューする。
   それ以外のWI作成専用セッションで以下のいずれの条件にも該当しない場合は、メイン自身による意味の検収と機械チェックのみで保存できる。
   独立した検収が必要な原稿についてもメイン自身が起草し、投入前レビューを経た上で保存する。
   単に要求の件数が多いことだけを理由に、起草や保存を別の担当エージェントへ委譲しない。

   - 1つの原稿に複数の要求単位が含まれている、または複数のリポジトリを扱う。
   - 観測された欠陥の原因分析を必要とする。
   - 対象や由来の分類、公開契約に関する判断、要求の一部の採用、または未確定事項を含んでいる。
   - 範囲語や開放列挙、確認質問への回答から確定した要件、または他のAWIへの割り当てを含んでいる。

2. 直接起草する場合、メインエージェントは`draft-semantic-review.md`に基づく意味の検収を自ら実施し、原文と反映内容を読み比べ、構造、表記、重複も確認して保存する。
   投入前レビューは、add-awi-by-userのすべての原稿と、それ以外では上記の条件のいずれかに該当する原稿に対して実施する。
   ユーザー向けに提示する部分は、検収側がまず自ら判定し、読者別の探索サブエージェントを起動するかどうかは`draft-semantic-review.md`に定める2つの条件に基づいて選択する。
   同一の作成主体が並行して準備した複数の原稿は、要求、出所、証拠を要求識別子で対応付け、`share/wi-draft-review.parent.md`の複数原稿入力仕様を用いて共通の事前準備を共有する。
   後続の原稿を待つためだけに待機することは避け、合格した要求から他の要求の修正待ちとは独立して保存を進める。
   他の作業に付随して投入する場合の文脈分離と、親エージェントによる意味の検収は引き続き維持する。
   方針段階から本文段階へ進む場合は、`share/wi-draft-review.parent.md`の`本文確認`を使う。
