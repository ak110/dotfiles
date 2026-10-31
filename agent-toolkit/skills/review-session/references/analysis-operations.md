# 原因分析の運用

## 規範の未適用と目的逸脱

規範が適用されなかった事象と、規範を適用した結果として目的から逸脱した事象は、原因を切り離して分析する。
以下で実際のモデル階層を規定と比較するのは、その情報が対策の選定を左右する場合に限る。
規範本文と必要な変更履歴で欠陥と修正方針を判断できる場合は、記録位置やモデル階層を追加で調べずに調査を終える。
問題発生前のコンテキストにおいて規範の存在判定が`present`であった未適用事象では、記録から確認した実際のモデル階層（上位・中位・軽量）を、`agent-toolkit:delegation`の`references/runtime-routing.md`「代替時の組合せの目安」に照らし合わせる。上位階層あるいはそれと同等以上と確認できるモデルの場合は、モデルの性能不足を原因とみなさず、新たな条文の追記よりも先に、既存条文の削除、緩和、集約、単純化、または一般化を検討する。中位や軽量モデルの場合は、条文が明確であり、権限・手段・入力が適切に揃っていたにもかかわらず失敗したときに限り、工程に割り当てるモデルの変更やモデル自体の改善待ちを対策の候補とする。モデル階層に基づく評価は記録から確実に特定できたモデルのみを対象とし、モデルが不明または記録から欠落している工程については推測で補わない。

規範の問題の有無を問われた場合は、まず規範本文を確認する。本文の確認や必要な変更履歴との比較で欠陥が確定した場合は、セッション記録の照会を省いて規範の是正へ進む。
規範本文に欠陥が見当たらず、未適用の区分によって対策が変わる場合に限り、以下の手順で記録を照会する。

1. 条文から特徴的な1行を選び、`atk run-script session-review-evidence --`の`--context-at <事象の記録位置> --phrase=<文字列>`を実行してコンテキスト内の存在を照会する。得られた判定結果と、条文がコンテキストへ読み込まれた経路を示す`channel`をAWIの`直接的原因`行に記録する。phraseには事象発生時点の条文から文字列を選ぶ（事象後に改訂されている場合は`git log -S`で当時の文面を特定する）。
2. 判定結果を各区分に対応付ける。`absent`は起動契機の欠落や参照時点の問題（条文を含む資料が読み込まれなかった）とする。`dropped-by-compaction`はコンテキスト圧縮時の問題（会話圧縮によって除外され、再読み込みの対象外だったか切り詰めによって欠落した）、`present`は条文の判定条件、責務の所在、または想起性の問題とする。一致した`channel`が`user`と`assistant`のみである場合は、会話の中での単なる言及として扱う。
3. `agent-basics:bugfix/references/root-cause-remediation.md`「再発防止策の必須性」の未適用規範に関する是正原則に従って原因を特定する。条文の想起性を改善する案と、改善後の条文によって誤りの発生を未然に防げる見込みの根拠をAWIへ記録する。その見込みは、`agent-toolkit:delegation/references/agent-documents-basics.md`に示されている読み手（LLM）の特性に基づいて評価する。

目的逸脱には、規範に従ったことによる処理の停止、差し戻し、空転、不要な反復、条文間の矛盾、前提の欠落、あるいは要求を満たさない成果物の生成などが含まれる。正常に完了した工程であっても、資料の読み込み、照会、事前準備、確認に要した作業量を、本来達成すべき目的の操作と比較して評価する。
規範に従って実行される工程は適用されるたびに発生するため、1セッション内で1回発生しただけでも反復的な負担として評価する。ツールの呼び出し回数、資料の読み込み量、所要時間、適用の頻度を考慮し、必読範囲の縮小や集約、準備処理を呼び出される側へ移管する案、または探索自体を不要にする案などを比較検討する。
条文を改訂する際は、本来保護すべき価値、導入目的、および期待される成果に対応付けて内容を整理する。現行の条文をそのまま維持する場合は、同様の停止が発生する条件とそれを許容する理由を記録する。
判断の到達可能性については、`agent-basics:review-standards`の`references/judgment-details.md`「検収手段の到達可能性」に従って確認する。一般的な設計案の比較評価は、`agent-basics:writing-standards/references/design-heuristics.md`に従って行う。

## 参照資料の取得

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:delegation/references/runtime-routing.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/runtime-routing.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:bugfix/references/root-cause-remediation.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-remediation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:delegation/references/agent-documents-basics.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/agent-documents-basics.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:review-standards/references/judgment-details.md` | 判断 | `read:agent-basics:*/skills/review-standards/references/judgment-details.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-basics:writing-standards/references/design-heuristics.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-heuristics.md` |

## 通知の前提が成立しない場合

- フックやツール、実行環境からの通知が担当外の対象、実行不可能な処置、あるいは実在しない前提を示していることがある。この場合は個別対応とは別に、通知機構自体の欠陥として処置する（同一セッション内での是正、`agent-toolkit:wi-standards`に従ったAWI起草の提案、または同じ欠陥を扱う未完了WIの参照のいずれかを行う）

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-toolkit:wi-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` |
