# 承認範囲の判定：逸脱

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `user-confirmation-and-report-authorization.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/user-confirmation-and-report-authorization.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `user-confirmation-and-report-decisions.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/user-confirmation-and-report-decisions.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `procedure-conflict.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/procedure-conflict.md` |
| 経験の記録から承認の適用範囲を判断する前 | `agent-basics:writing-standards/references/investigation-experience.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation-experience.md` |

## 逸脱

規範からの逸脱と、ユーザーが確定した手段に関する方針からの逸脱は、`agent-toolkit/share/rules/common/01-agent.md`「QCDと3段判定」の条件を満たす場合に限り実施する。
手段側の判定は`user-confirmation-and-report-authorization.md`「認可を要する操作」の基準に従う。

- 逸脱の単位は、対象となる規範条文や方針の記録と、実際に逸脱が生じた場面の組み合わせを1件として扱う。
- 規範からの逸脱は、事前の承認対象からは除外する。
  その代わり、逸脱1件ごとに該当する規範の場所・場面・双方の案のQCDを比較した観測根拠を作業完了報告の`### 自ら確定した判断`に1行で記録する。
  自律モードの場合は、報告用UWIの同じ見出しにも記載する。
  ユーザーはこの報告を通じて逸脱内容を確認し、必要に応じて是正を指示できる。
- 手段に関する方針からの逸脱についても、まず`user-confirmation-and-report-decisions.md`「確認要否の判定」を適用する。
  目的・要件・認可、および期待される結果を維持したまま技術とQCDの観点から判断できる場合は、エージェント自身で方針を確定し、規範からの逸脱と同様に報告へ記載する。
  単に手段が異なることだけを理由に確認を発行することはない。
- 調査し既存の認可を適用した後も、ユーザー独自の選好や認可、適否の判断が残る逸脱については、判断が必要な項目ごとにユーザー確認する。
  確認文には、該当する方針の場所・場面・QCDの観測根拠に基づく比較・具体的な実施内容を記載し、対話での確認時には復元方法も併記する。
- 事前承認を要する副作用（第三者への影響、外部サービスの状態変更、復元に追加の外部操作を要する副作用、破壊的操作）を伴う逸脱は、事前承認として扱う。
- ユーザーの目的や要件がシステムの硬い制約と両立しない場合は、事前承認の対象とする。
- 複数の規範が互いに衝突している場合や、手順の文言が現実に即していない場合であっても、各規範の目的に照らして次の工程を一意に判断できるときは、事後承認を求めずに`references/procedure-conflict.md`の判定順3と報告の手順に従う。
