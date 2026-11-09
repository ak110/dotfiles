# レーンの実行：上流投入

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 目標起点の判断を行う前 | `lanes-goals.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/lanes-goals.md` |
| レーンの専用worktree・所有登録を作成・再利用・回収する前、`レーン開始時刻`を記録する前、後段レーンの先行調査を始める前 | `lane-resources.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/lane-resources.md` |
| 本文で基準を使う資料の選択・取得時 | `../../../share/add-wi.parent.md` | 判断 | `read:agent-toolkit:*/share/add-wi.parent.md` |
| 追加WIと即時修正を区別する前 | `immediate-fix.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/immediate-fix.md` |

## 上流投入

**1.** レーン担当を起動する前に、`上流投入`が`なし`以外のすべての項目について、`${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`の上流投入手順に従って委譲する（同一の`元項目`に属する組は同一の投入担当へ委譲する）。
投入が完了した上流項目は、返却値に含まれる`ファイル名`によって識別する。
レーン起動に先行して投入する理由は、`混在`区分の項目において上流へ引き渡した要求を、レーン担当が実装対象から除外できるようにするためである。
「続行できない理由」を返してきた要求を含む項目については、`add-wi.parent.md`「受領と検収」に従って元項目の状態を変更せずに保留とする（`混在`区分の場合はレーン自体の起動も保留する）。

**2.** `上流要求だけ`（`レーン`が`なし`）の項目は次のとおり扱う。

- `completed`の要求が含まれる場合: レーンは起動せず、取得したすべての`ファイル名`を`atk wi set-dependencies`を用いて元項目の`depends_on`へ追加し、`atk wi return-to-inbox`で`inbox`へ差し戻す
- すべての要求が`condition_not_met`の場合: 依存関係は追加せず、条件不成立の理由や調査によって確定した投入見送りの結論を、根拠と証拠の所在に基づいて確認する。
  人間由来の要求を投入見送りとする判断は、`../../wi-standards/references/origin-and-approval-origin.md`「承認」に従って確定した上で、元項目の採否を確定する

**3.** `混在`区分の項目では、投入結果（要求内容、投入先、`ファイル名`、`結果`）を`exec.parent.md`の`上流投入結果`として渡す。
`completed`の要求が含まれる項目については、統合指示における`AWI終了区分`を`終了しない`に設定し、統合の検収完了後にメインエージェントがすべての`ファイル名`を`depends_on`へ追加した上で`inbox`へ差し戻す。
すべての要求が`condition_not_met`となった場合は、通常の採否確定を適用する。
