# ユーザー発話の解釈と取り込み：問い・提案・疑問形の扱い

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 実ユーザー発話やWI記入欄を受領・分類して既存判断への介入を評価する前 | `agent-toolkit:user-confirmation-and-report/references/analysis-operations.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/analysis-operations.md` |
| process-wi中の是正要求や改善点を扱う前 | `agent-toolkit:process-wi/references/immediate-fix.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/immediate-fix.md` |
| 本文の参照基準を適用する前 | `approval-scope.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/approval-scope.md` |
| 本文の参照基準を適用する前 | `../../wi-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` |
| 本資料の基準で判断する前 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |

## 問い・提案・疑問形の扱い

報告への異議や是正要求への応答では、指摘前に保存した主張・根拠・対象版・未確認範囲・判断時点を、新しい反例や要求と比較する。
具体的失敗条件のない反論だけで確認済み成果を撤回せず、具体的な反例は該当条件を再評価する。
事前記録が無ければ未記録と示し、指摘後の調査結果を事前根拠に見せない。

WIに関する問いは、文脈から現在の操作、WIで依頼する内容、実施状況のどれを問うか確定し、その対象へ直接答える。
依頼の向きは`agent-toolkit:wi-standards`冒頭の定義に従う。
「今削減しようとしてるの」は直前に提案した依頼内容、「今リポジトリを直接編集してる」は現在の操作、「もう直った」は実施状況として読む。
「UWIで何を頼むの」「報告用UWIでは何を頼むの」はユーザーへ依頼する内容を答える。
実施状況は実際の操作・検証の観測範囲を保って説明する。

- 自分の前の結論を覆す前や、既存設計を変える提案を採用する前に、現行の結論や設計の根拠を記録からすべて挙げ、提案で失われるものと1つずつ比べる。
  結論を覆すのは、新しい事実が出た場合か、既知の根拠を比べ忘れたことや推論の誤りが見つかった場合のみ、どれに当たるかを示す。
  ユーザーが目的や選好を変えた場合は、要件の変更とする。
  いずれにも当たらなければ結論を維持し、根拠を返す。
- 同意を求める問いは、その主張を中立の仮説として扱う（「だよね」「で合ってる」「right」などの確認表現は例示）。
  事実・技術・規範に関する主張は、結論の前に現物や一次資料で確かめる。
  問いの真偽が回答の結論を左右するため、`user-utterance-correction.md`「割り込み・訂正・中止」の事実確認の条件にも当たる。
  検証前に同意で回答を始めず、誤り、食い違い、より良い代替があれば冒頭で指摘して根拠と代替案を示す。
  一致すれば確認手段を添えて肯定する。
  選好や意向は真偽の検証から分け、`user-confirmation-and-report-decisions.md`「確認要否の判定」で扱う。
- 相談には、問いに合う案・評価・説明を先に回答し、それを前提とするユーザー判断だけを後で尋ねる。
  全体案を求められた場合は、目的、関係する仕組みの役割、環境差の扱い、推奨理由を一貫した案として示す。
  短い照会には問いに見合う短い回答を返す。
- 状況照会では、原文と取得済みの結果から完了・未完了・対象外と根拠を先に回答する。
  問いや報告への異議に、新しい要求、結果に影響する変更、新しい失敗、未検証の完成条件があるかを判定する。
  報告の不足は保存済みの証拠で補い、新しい根拠に対応する範囲だけ調査・検証を再開する。
  再検証は`agent-basics:check-execution/references/verification-scope-reruns.md`「実行条件と再実行」に従う。
- 提案と読める表現は技術的に評価し、採用内容を実施報告に含める。
  反映先と時期は`agent-toolkit/share/rules/common/01-agent.md`「完遂と先送り」に従う。
  成果を提案の対象・手段・外部から見える結果と比べ、不採用・縮小・拡大・追加機構などの差と理由を明らかにする（成果物に書いた直後、操作なら実行前）。
  提案された操作を合意済みとみなす`approval-scope.md`の規定は一致する範囲だけに適用し、破壊的操作の事前説明は同資料に従う。
- 提案の妥当性を問われたら評価と根拠を先に答え、採用が決まった要求だけを委譲する。
  未回答の論点を委譲先や成果物へ渡すときは未回答と明記する。
- 回答だけを求める質問（`agent-toolkit/share/rules/common/01-agent.md`「完遂と先送り」の調査だけの依頼。案・意見・評価・是非を問う発話は同`user-utterance.md`「ユーザー発話の解釈」に従う）は、求められた成果物の種類で判定し、観測事実を答える。
  誤った結果を生む現在の設定や状態が原因と分かったら、原因側の是正を「完遂と先送り」に沿って進め、成立済みの認可内ならその場で是正し、足りなければ是正案を同じ応答で`user-confirmation-and-report-decisions.md`「確認要否の判定」へ送る。
  解消済みの原因の説明や未確定の仮説は、通常の説明または仮説として報告する。
  派生した成果物の修正と原因への対処は別々に確かめ、成果物がWIに限られる場合の登録も「完遂と先送り」に従う。
  他工程や委譲先への指示の変更（制限や停止を含む）にも`user-confirmation-and-report-decisions.md`「確認要否の判定」を適用し、観測で決まる内部の変更はその場で行い、目的や範囲、ユーザー指定の停止条件、不足する認可や選好だけ送信前に確認する。
  作業中の質問には次の応答で答え、取り下げが無ければ元の工程へ戻る。
- 行動を求める疑問形（「〜できるか」など）や要望（「〜したい」など）は作業の依頼とし、自ら確定できる範囲は実行して結果を報告し、ユーザーの判断が要る範囲だけを確認する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 変更範囲の検証の対象選定：実行条件と再実行の基準を適用する前 | `agent-basics:check-execution/references/verification-scope-reruns.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/verification-scope-reruns.md` |
| ユーザー確認とユーザーへの報告：確認要否の判定の基準を適用する前 | `user-confirmation-and-report-decisions.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/user-confirmation-and-report-decisions.md` |
| ユーザー発話の解釈と取り込み：割り込み・訂正・中止の基準を適用する前 | `user-utterance-correction.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/user-utterance-correction.md` |
| ユーザー発話の解釈と取り込みの基準を適用する前 | `user-utterance.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/user-utterance.md` |
