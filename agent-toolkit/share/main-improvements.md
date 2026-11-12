# メインエージェントの改善点報告

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 改善点の処置とAWI化ステータスを決める前 | `agent-basics:bugfix/references/root-cause-prevention-requirements.md` |
| 待機中に改善の投入や独立作業を前倒しする前 | `../skills/delegation/references/waiting-and-monitoring-independent-work.md` |
| process-wiで改善の対処時機を決める前 | `../skills/process-wi/references/immediate-fix.md` |
| 規範読込と委譲結果の改善報告を受領する前 | `../skills/delegation/references/receiving-followup.md` |
| 当座の訂正と再発防止を判断する前 | `agent-basics:bugfix/SKILL.md` |

## 作業中の改善点の報告

改善点は`rules/common/01-agent.md`「事実と推論」で選び、発見時に処置・AWI化を決める。
報告行の書式・区分は`rules/rules-main.md`「進捗報告」のテンプレート直下に定義する。

process-wiの対応時機は読込表の`immediate-fix.md`で判定する。
登録と修正を区別し、転記も同書式を使う。
`不要`の定義は`agent-basics:bugfix/references/root-cause-prevention-requirements.md`「再発防止策の必須性」を参照する。
all-repos統括を含む全メインは、待機中に判定できる改善を発見時に既存項目と比較してAWIを保存する。
登録時機と`予定`の定義は読込表の「待機中の先行実施」で判定し、前提・再開条件・時点を作業記録へ残す。
最小調査をその場で行い、当座の訂正・再発防止は`agent-basics:bugfix`、Q影響・続行/規範不良は`rules/common/01-agent.md`「完遂と先送り」で判断する。
本題外はagents_serverへ渡し、担当外は根拠と未確認事項を引き継ぐ。
証拠は非同期振り返りで再利用し、既調査の再調査・同期振り返りは終了条件から除く。
規範読込と委譲結果は`../skills/delegation/references/receiving-followup.md`「受領後の扱い」で報告・集約する。
全モードで受領時に処置し、次の発話でユーザーへ転記する。

## 停止の回避策の共有

工程の停止・遮断・同じ工程の反復を生む事象の回避策を確定したら、その工程内で、並行・後続のセッションが開始時に読む既存のホスト固有の規範へ記録する。
同じセッションで是正が完了して並行・後続へ反映される場合は是正を優先し、この記録は不要とする。
是正が権限外、配布・再起動・外部対応待ち、または正式対応をAWIへ渡した場合が対象となる。
記録先に配布元があれば、その配布元と編集手順に従う。
記録先が無ければ新しく作らず、記録しなかったことと記録案をユーザーへ報告する。
記録には事象、観測値、回避策、対応状況、削除条件を書く。
対応状況には正式対応の受渡し先（AWI名か題名）か正式対応を要しない根拠と、是正が有効になる時点を含める。
MCP・hook・スクリプトの変更が稼働中のセッションへ未反映なら、その範囲も明示する。
削除条件が成立したら記録を削除する。
