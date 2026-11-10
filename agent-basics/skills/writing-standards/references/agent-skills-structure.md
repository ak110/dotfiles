# Agent Skills実装ガイドライン：SKILL.md本体とreferences/の役割分担

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-documents-additions-volume.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-additions-volume.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-documents-basics-read-tables.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-basics-read-tables.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md` | 判断 | `read:agent-basics:*/skills/writing-standards/SKILL.md` |

## SKILL.md本体とreferences/の役割分担

- 付属資料は`skills/<skill-name>/references/`配下に配置し、特定のスキルに属さない参照文書は別名のディレクトリに配置する（努力目標。どのスキルに属する資料かを判別しやすくするため）
- `SKILL.md`本体には、すべての対象に共通する原則を記載する。
  プログラミング言語やトピック固有の細則は`references/`配下に配置し、条件別資料の分割は`agent-documents-basics-read-tables.md`「読込表」の方針に従う
- トリガー条件、中心となる手順、停止条件などの必須指示は、記述量の削減が求められた場合でも本体冒頭に残す（本体の後半部分は起動判定の時点で読み飛ばされやすいため）。
  それ以外の補足的な記述は、`agent-documents-additions-volume.md`「文書記述量の管理」に従って`references/`への退避を優先して検討する
- 規範の記述場所を探す際は、本体と`references/`の双方を確認する

## 呼び出し側プロンプトの責務分離

スキルの詳細な動作、条件分岐、引数を省略した際の動作や対象の選定、引数の仕様は`SKILL.md`本体に集約する。
呼び出し側のプロンプトには、起動指示とその呼び出し固有の値を渡す引数のみを記述する。
呼び出し側に動作の詳細を記述すると、呼び出し元ごとに挙動の不一致が生じる原因となる。

## 失敗事例の反映

繰り返し発生した失敗事例への対策は、上位の共通原則を抽出した上で、既存の節の箇条書きに統合する。
満たすべき性質と判断条件を明記し、禁止事項の羅列や対比表を単に追加する構成は避ける（努力目標。読み込み量が増大する上、列挙されていない事項は許可されていると誤解される恐れがあるため）。
