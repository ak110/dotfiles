# メインエージェントの改善点報告

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 改善点の処置とAWI化ステータスを決める前 | `agent-basics:bugfix/references/root-cause-prevention-requirements.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-prevention-requirements.md` |
| 待機中に改善の投入や独立作業を前倒しする前 | `../skills/delegation/references/waiting-and-monitoring-independent-work.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring-independent-work.md` |
| process-wiで改善の対処時機を決める前 | `../skills/process-wi/references/immediate-fix.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/immediate-fix.md` |
| 規範読込と委譲結果の改善報告を受領する前 | `../skills/delegation/references/receiving-followup.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/receiving-followup.md` |
| 当座の訂正と再発防止を判断する前 | `agent-basics:bugfix/SKILL.md` | 判断 | `read:agent-basics:*/skills/bugfix/SKILL.md` |

## 作業中の改善点の報告

改善点は`rules/common/01-agent.md`「事実と推論」で選び、発見時に処置・AWI化を決め、次の書式で報告する。
`気付いた改善点: <事象と対象のツール・手順・条文>（AWI化ステータス: 済 <WI名> | 予定 <区分> <再開条件> | 即時対応 | 不要 <区分> <理由>）`

- `済`: 保存したWI名を添える
- `予定`: 登録の前提待ち。
  区分は`判断待ち`・`認可待ち`・`入力待ち`・`書込競合`とし、再開条件を添える
- `即時対応`: 今回是正する
- `不要`: 独立した再発防止策の登録なし。
  区分は`非欠陥`・`処置済み`・`被覆`・`見送り`とし、理由を添える

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
