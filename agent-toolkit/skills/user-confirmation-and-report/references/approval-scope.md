# 承認範囲の判定

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `user-confirmation-and-report-authorization.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/user-confirmation-and-report-authorization.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../wi-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `user-confirmation-and-report-decisions.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/user-confirmation-and-report-decisions.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../wi-standards/references/uwi-format.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/uwi-format.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `procedure-conflict.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/procedure-conflict.md` |
| 経験の記録から承認の適用範囲を判断する前 | `agent-basics:writing-standards/references/investigation-experience.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation-experience.md` |

## 承認源と適用範囲

ある操作が`user-confirmation-and-report-authorization.md`「認可を要する操作」に該当するかどうかは、その操作がもたらす副作用に基づいて判定し、操作の意図や目的は判定基準から除外する。
既存の環境、成果物、あるいは依存関係を置き換えたり、再作成したり、削除したりする操作は、調査や原因特定、動作確認を目的とする場合であっても、破壊的操作として承認を求める対象とする。
エージェント由来の残存資源と誤変更の回収・是正は、`agent-toolkit/share/rules/common/02-agent-operations.md`「プロセス終了の安全規定」の共通判断を適用する。
現在の利用と必要な成果を保護し、既存の認可で不要と確定できる対象は追加確認なしで処置する。
所有セッションの違いだけで確認へ回さず、調査後も未確定のユーザーの意図・必要性・認可が残る場合に確認する。

ユーザーが提案として指定した操作は、その提案内容の範囲内であれば合意済みとみなし、追加の確認なしでそのまま実行する。
承認の根拠（承認源）となるのは、ユーザーからの明示的な指示、プロジェクト規範、作業手順、および人間が作成したWIである。
人間由来のWIから承認が成立する条件は`agent-toolkit:wi-standards`が定める。
それ以外の承認源では、指示・規範・手順が明示している範囲を承認範囲とする。
また、計画ファイルに記録されたユーザー確認への回答も承認源として扱う。
一度成立した承認は、承認源の種類を問わず、操作内容・対象・影響範囲・成立条件が合致する範囲へそのまま適用する。
特定の範囲に限定して受け取った指示は、その限定条件が有効な間、かつ同一の対象に対する操作にのみ適用する。

過去の会話や作業記録、メモ、自動読み込みされた記憶などに含まれる承認も、元の承認源と成立条件へ戻って今回の操作と比較する。
記録に限定条件が保存されているかと、今回その条件が成立するかを分けて確かめ、合致する範囲では成立済みの承認を再確認せず利用する。
低リスク作業で確認を省略した経験や速度優先の手順だけを根拠に、別の対象・操作・影響範囲へ認可を広げない。
経験全般の適用判断は`agent-basics:writing-standards/references/investigation-experience.md`「経験の保存と適用」に従う。

`user-confirmation-and-report-authorization.md`「認可を要する操作」で定めているCI関連の外部サービス設定に関する包括認可には、外部サービスの状態変更、第三者への影響、既存設定の置換や無効化、およびCI用シークレットの作成・変更が含まれる。
破壊的操作する際は、安全に対象を特定し、影響範囲と復元方法を実行前に説明する義務を維持したうえで、操作結果をユーザーへ報告する。
一方、CIと関係のない独立した副作用や、目的・要件の変更、実行環境による権限拒否については、従来どおり個別の確認基準を適用する。

## 事前承認と事後承認

有効な承認がまだ得られていない承認対象（「認可を要する操作」および`user-confirmation-and-report-decisions.md`「確認要否の判定」で確認が必要と判定された変更）は、事前承認と事後承認のいずれかに分類する。

- 事後承認: 変更が可逆であり、第三者への影響、外部サービスの状態変更、および復元に追加の外部操作を要する副作用をいずれも伴わない場合に適用する。
  エージェントが最善と判断した対応を実行したうえでユーザーに確認を求め、回答を待たずに元の作業を続行する。
  実行レビューを伴う作業では、実行レビューが完了して内容が確定した時点の変更を用いて事後承認を求める。
  この確認時期は、起動方法や確認手段にかかわらず適用する。
  確認文には実施した内容・判断の根拠・影響を記載し、対話での確認では元の状態へ戻す復元方法もあわせて案内する。
  UWIに記録する場合は、`agent-toolkit:wi-standards`の`references/uwi-format.md`に定める情報選択基準に従う。
- 事前承認: 上記の副作用のいずれかを伴う操作に適用する。
  ユーザーとの合意を得た後に作業へ着手する。
- 成果物が適切かどうかをユーザーしか判断できない変更についても、必要な認可やユーザーのみが持つ判断材料に基づいて「確認要否の判定」を適用する。
  実行環境の制約などで検証できなかったことだけを理由に承認を求めるのは避け、実施できた確認内容とその限界を報告する。

起動中のスキル、適用中の規範、ユーザーの指示、あるいは計画ファイルに記録された元の要求や確認回答によって、作業工程・対象範囲・許容される副作用が明確に定義されている場合、その範囲内で実施した内容は通常の完了報告に含める。
その範囲内であっても、規範から一意に導けない判断や、ユーザーの選好に依存する代替案、あらかじめ定められた範囲を変更する操作を追加した場合は、その追加部分についてのみ`user-confirmation-and-report-decisions.md`「確認要否の判定」を適用する。

ユーザーが確定させた目的・要件、およびユーザーが観測する結果に関わる判断の変更（`user-confirmation-and-report-authorization.md`「認可を要する操作」）は、可逆性や外部影響の有無、観測可能性にかかわらず、すべて事前承認の対象とする。
この規定は、本書に定める事後承認の各条件よりも優先して適用する。
事後承認として進めると、ユーザーが把握しないまま目的側の決定が変更されてしまうためである。
なお、判断を記録した文書自体が変更時の承認手続きを別途定めている場合は、その指定に従う。

破壊的操作を実施する際は、実行する内容・影響範囲・復元方法を事前に説明する。
この説明は、ユーザーから提案された破壊的操作であっても同様に実施する。
パターンマッチ、ワイルドカード、状態指定などを用いて対象を一括選択する破壊的コマンドを実行する場合は、事前に同じ指定で実際の対象一覧と副作用を取得し、影響範囲を提示したうえで、依頼の範囲外となる対象を除外する。
ユーザーに実行を依頼するコマンドを案内する場合も同様の手順を踏む。
一括指定の展開結果は、起草時の想定よりも広がる可能性があるためである。

## 条件別資料

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 公開操作の消費主体に関する判断・作業の前 | `approval-scope-public-operations.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/approval-scope-public-operations.md` |
| 逸脱に関する判断・作業の前 | `approval-scope-deviations.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/approval-scope-deviations.md` |
| 新しい挙動と準備の提示に関する判断・作業の前 | `approval-scope-preparation.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/approval-scope-preparation.md` |
