# 振り返りの判断基準：定義本文の読込の判定

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 通読で比較するとき、または規範の未適用と目的逸脱を分析する前 | `agent-toolkit:review-session/references/analysis-operations.md` | 判断 | `read:agent-toolkit:*/skills/review-session/references/analysis-operations.md` |
| 効果を記録する前 | `../../wi-standards/references/states-and-dependencies.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/states-and-dependencies.md` |
| 類似見直し前 | `agent-basics:bugfix/references/root-cause-remediation.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-remediation.md` |
| 経験を知見として保存・再利用する前 | `agent-basics:writing-standards/references/investigation-experience.md`、`agent-basics:writing-standards/references/agent-documents-basics-placement.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation-experience.md`、`read:agent-basics:*/skills/writing-standards/references/agent-documents-basics-placement.md` |

## 定義本文の読込の判定

`definition-body-read`は明示的な読取操作か、静的に読取操作と対象を対応付けた材料である。
`definition-read-unresolved`は取得の有無を静的に決められない材料で、`calls`や個別証拠の`definition_reads`に元の記録位置、入力、結果の位置、操作ごとの理由を持つ。
準備は`definition-body-read`の各読取を`launch_check`で判定する。
同じ記録で読取より後に同じ役割を起動した読取を起動準備の疑いあり（`suspected`）とする。
後続に同じ役割の起動が無い読取と、読む主体の役割文書と同じ手順書の読取は判定済み（`cleared`）とする。
起動の判定には`start`の`subagent_md_path`、Codexの外側セルの`start(`呼出しの同じ引数、手順書のファイル名を入力に含む`Agent`などの起動を使う。
メインエージェントが分類するのは、`suspected`の読取と`definition-read-unresolved`の全発生位置である。
`cleared`の読取は判定理由（`reason`）を確かめれば足り、`suspected`の読取を含まない`definition-body-read`の候補は評価必須から外れる。

Codexの記録では、`functions.exec`に渡したJavaScriptの1回の実行を「外側セル」、その中で起動したコマンドの`CommandExecution`記録を「内側実行」と呼ぶ。
`atk run-script session-review-evidence`は、同じturnで外側セルの呼出しから結果までの区間にあり、帰属が一意に決まる内側実行の実際のコマンド文字列を解析する。
外側の`call_id`と内側の`item.id`は別の識別子として扱い、一致や完了順は対応付けの根拠から除く。
候補の`calls`の各要素を`tool`で区別して位置を読む。
名前空間を除いた`tool`の末尾の名前が`exec`なら、その要素の`line`・`result_line`は外側セル、`operations`の各要素の`execution_line`・`execution_result_line`は対応付けた内側実行の位置である。
これには実記録の`exec`と名前空間付きの`functions.exec`を含む。
対応付けられない内側実行は、`tool`が`CommandExecution`である独立した`calls`要素となり、その`line`・`result_line`が内側実行を指す。
個別証拠の`definition_reads`も同じ構造である。
メインエージェントは各位置から入力と結果を確認し、対応付けがない場合は同じturnの外側セルの原記録も確認する。
この対応付けはCodexの外側セルの記録だけに適用する。

外側セルの入力だけでは読取の有無を決められず、内側実行も対応付けられない場合は、セル全体の入力と対応付け不能の理由を1つの未確定操作へまとめる。
理由は、外側セルの結果記録が無く境界を確定できないこと、同じturnの区間が重なり帰属が曖昧なこと、または区間内で完了済みの内側実行を確認できないことである。
最後の理由には、コマンドが継続中で完了記録が後続する場合も含む。
この場合、入力別の未確定理由は集約された理由に置き換わるため、保存されたセル全体の入力と原記録から不足する根拠を確認する。
同じ外側セルに含まれる静的に確定した読取は、同じ`record`・`line`を持つ別区分の候補`definition-body-read`に残る。
静的に非読取と判定した操作は候補の出力対象から除く。
内側実行との対応付けができた場合など、入力別の未確定操作が残るものは各操作の`reason`を参照する。

取得の有無、目的、結果の成否は別々に判定する。
編集対象の本文、JSON参照、履歴の検索条件、他の操作の引数に含まれる定義名は、名前があるだけでは取得の根拠にしない。
入力が未確定なら元の記録へ照会し、根拠が足りないものは`未確定`に残す。
記録中のコード自体は評価対象から外す。
取得との対応を確認できた位置は、起動準備、編集作業、自身が受領した定義、返却後の検収、目的不明の取得のいずれかに分け、結果の成否と不足する根拠を個別に記録する。
判断の根拠は、呼び出した主体と対象、元記録の本文と実行結果、前後の起動・編集・返却の文脈に求め、近くの編集操作や役割名の一致だけで目的を決めない。
編集作業、自身が受領した定義、返却後の検収は、根拠を示して`definition-read-context`で判定済みとする。
起動準備の読込と確認できた位置は`../../delegation/references/delegation-sending.md`「送信」の禁止事項への違反とし、原因、対策、対策の保存先を対応付ける。
処理の成功、単発の発生、費用の多寡にかかわらず、この違反には対策を立てる。
分類に要る証拠が足りない位置と、対策が未確定の違反は、報告の`未確定`欄に残す。
対策の保存を確認した時点で処置済みとする。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 委譲の基準を適用する前 | `../../delegation/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/delegation/SKILL.md` |
