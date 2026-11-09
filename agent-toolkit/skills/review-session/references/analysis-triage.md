# 振り返りの判断基準：一次選別

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文で基準を使う資料の選択・取得時 | `agent-toolkit:user-confirmation-and-report/references/analysis-operations.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/analysis-operations.md` |
| 本文で基準を使う資料の選択・取得時 | `agent-basics:writing-standards/references/claude-hooks-block-warn.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/claude-hooks-block-warn.md` |
| 本文で基準を使う資料の選択・取得時 | `agent-toolkit:user-confirmation-and-report/references/auto-mode.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/auto-mode.md` |
| 本文で基準を使う資料の選択・取得時 | `agent-basics:bugfix/references/root-cause-severity-impact.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity-impact.md` |
| 通読で比較するとき、または規範の未適用と目的逸脱を分析する前 | `agent-toolkit:review-session/references/analysis-operations.md` | 判断 | `read:agent-toolkit:*/skills/review-session/references/analysis-operations.md` |
| 効果を記録する前 | `../../wi-standards/references/states-and-dependencies.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/states-and-dependencies.md` |
| 類似見直し前 | `agent-basics:bugfix/references/root-cause-remediation.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-remediation.md` |
| 経験を知見として保存・再利用する前 | `agent-basics:writing-standards/references/investigation-experience.md`、`agent-basics:writing-standards/references/agent-documents-basics-placement.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation-experience.md`、`read:agent-basics:*/skills/writing-standards/references/agent-documents-basics-placement.md` |

## 一次選別

正常回収・反復だけを問題化せず、分析表・点検記録は保つ。

通読で見つけた問題は同じ事象を指す候補とまとめ、対応する候補が無い問題も独立して原因分析へ進める。
候補の種別を問わず、期待された契約、実際の不成立、発生条件を判定する。
本文の受領、追送、代替手段などで回復していても根本原因を分析し、回復の事実と要した操作を別に記録する。
遮断や警告を解消してから目的を達した作業も、全体の品質と総費用を後述の共通比較で評価する。
必要な裏付け調査、出力上限に合わせた分割読取、起動時のスキル展開、変更後の再確認、用途に応じた過去資料の参照などは、契約不成立が観測されない限り、回数、日付、ロールだけで誤りとしない。
規範が求める工程の量が過大かは`analysis-cost.md`「規範適用による目的逸脱」で判定する。
契約不成立という欠陥と、品質を保って費用や時間を減らせる改善機会とを分ける。
正常、必須、回復済みといった分類は欠陥の有無の結論であり、実施方法の採否は`analysis-cost.md`「コストの無駄の確認」の比較で決める。
成功した通常操作、正常な返却、良い手段の再利用も、準備から後始末までの全工程を同じ比較へ送る。
情報探索や参照の問題は、選別の段階から`analysis-actions.md`「情報探索の問題」に従い、探していた情報と得た証拠を対応付ける。
除外するのは、恒久対策が要らないと明らかな候補に限り、その根拠を「対策を見送った問題」に書く。
原因探索の深さと再発防止策の要否・完了基準は重大度で決める（`agent-basics:bugfix/references/root-cause-severity-impact.md`「QとC・Dの区別」）。
資格情報の発見と報告の発話は、`agent-toolkit/share/rules/common/01-agent.md`「完遂と先送り」の手順を終えていれば判定済みとする。
その除外区分には`../../user-confirmation-and-report/references/analysis-operations.md`「ユーザー介入と確認の判定」の`credential-report`を使う。
ただし過剰反応自体の是正をユーザーが求めた発話は分析対象とする。
作業中に既存の規範が適用されなかった問題は、条文があることで除外せず、確証の無い候補も分析へ送る。
過去の振り返りやキュー処理で不採用と確定した案は、新しい観測や要件の変更がある場合に限って再評価する。
資格情報の存在を指摘する`気付いた改善点:`も、報告対象の値は所在ごとの報告に対応付け、報告後に`credential-report`として判定済みとする。
報告対象外の値（Git管理しているファイルの値、ローカル開発環境・テスト環境だけで使う値）を報告しなかったことは、報告の欠落として扱わず`credential-report`で判定済みとする。

- 失敗したコマンドとツール: 複数の最上位セッションで反復した失敗シグネチャごとに、終了コード、実行コマンド、診断メッセージ、発生セッション数から原因を調べ、再発防止策か、対策を講じない理由を確定する。
  反復の有無は実際のログで判断し、反復が観測されないことだけで恒久対策の要否を決めない。
  単発の失敗は、遠回りや手戻りの原因と会話の流れから明らかな場合に限り分析する。
- 委譲返却: 正常完了しただけの返却は欠陥候補としての詳細照会から外してよく、その実施方法と費用は通読や統計から共通比較へ送る。
  それ以外の返却は本文まで確認し、想定外の事象を含む返却はその事象を評価する。
  `続行できない理由:`の行を含む返却（旧形式のステータス値で続行不能を示したものを含む）は評価必須の独立した候補（`escalation`）とする。
  後続の応答で回復した場合も、出力の切り詰めなど担当内で補える不足を理由に続行不能を返した判断と、続行不能の原因を評価する。
- hook通知: 候補にはblock通知かwarn通知だけが記録される（応答言語hookの警告は`response-language-notice`の件数だけ）。
  通知ごとに操作の目的、`--detail`で取った元記録、通知に従った後の結果を対応付け、出力自体の不良、呼び出し手順への規範未適用、通知の案内と操作の不一致を分ける。
  誤判定として継続した記録も、元の通知、適用条件、担当者の判断理由、実行結果を比較し、誤判定、判断の誤り、正常な支援に分ける。
  必要な対応を取らなかった事象は、通知の存在や後の回復だけで是正済みとしない。
  発生源ごとに`agent-basics:writing-standards/references/claude-hooks-block-warn.md`で、事前に防ぐ型か遮断後に対処する型かを判定する。
  遮断後に対処する型の発火は想定どおりの動作とする。
  ただし1回の対処で解消しない場合、同じ呼び出しで反復した場合、発生頻度で同文書の費用比較の結論が変わる場合は、行動への対策と機構自体の負担を`analysis-cost.md`「規範適用による目的逸脱」で比較する。
  事前に防ぐ型には、規範の改訂、判定条件の見直し、フックの撤去、遮断後に対処する型への移行から1件以上の改善案を対応付け、現状維持ならその発火が規範どおり不正な操作を止めた根拠を書く。
  事前チェック工程を足す案では、操作ごとに増える呼び出し回数を費用として明記する。
- 権限拒否: `../../user-confirmation-and-report/references/permission-denial-classifier.md`「偽陽性と判断できる拒否への対応」の適用条件を満たしていたか、満たしていればその対応が適用されたかを判定する。
  委譲先の拒否は委譲元の記録で判定する。
  条件を満たしたのに適用されなかった拒否は、作業進行への影響を問わず`analysis-actions.md`「原因と対策」へ送り、未適用の原因と必要な処置を判定する。
  パーミッションルールを変える案は、拒否時の文面と分類名を証拠に添え、`../../user-confirmation-and-report/references/auto-mode.md`のワークフローへ送る。
- 手順書の作業を一時スクリプトで代替した箇所: 有効期限のある資格情報の更新、設定の再登録、別環境の構築、定期的な運用作業など、将来再実行される手順かを手順書の本文と扱う値から判定する。
  再実行されるなら、スクリプトを手順を所有するリポジトリのスクリプト配置場所へ移して手順書から呼ぶ対策を、そのリポジトリを`target_repo`とする通常AWIへまとめる（同じセッションで是正済みなら除く）。
  この原稿を`analysis-actions.md`「原因と対策」へ引き渡す。
  1回限りの使い捨て作業は対象外とする。
- その場のコードによる加工: `adhoc-processing`の候補、通読で見つけた後加工、同じ目的のコマンドの反復について、呼び出しごとの目的（どの出力から何を取ったか）を特定し、反復性を判定する。
  同じセッションで同じ目的の加工が2回以上あれば反復とする。
  スキルや規範の手順が実行のたびに求める工程（逐語引用の文字位置の算出など）のための加工と、`agent-toolkit`のコマンド出力形式の制約で要った加工は、1回の観測でも反復とする。
  反復する加工は`analysis-actions.md`「原因と対策」へ送り、比較する代替案と評価順は`agent-basics:bugfix/references/root-cause-scenarios-investigation.md`「反復する加工の問題」に従う。
  反復しない1回限りの調査は、目的と反復しない根拠を「対策を見送った問題」に書いて除外する。
- `気付いた改善点:`で始まる行: その場の直接的原因・影響調査と必要な処置を確認し、根拠と判断を再利用する。
  未調査、変更、不一致の部分だけを補って残る分析を`analysis-actions.md`「原因と対策」へ送る。
  単純な効率改善もQCDで採否を決め、担当外の処置と引き継ぎ先を保持する。
  その場でユーザーへ伝えた行も対象に含め、非同期分析まで初動調査を延期したり、既調査事項を最初からやり直したりしない。

### ユーザー介入の判定規則

ローカルな終了操作と、それに付随する訂正・続行の指示は、共通判定の`non-changing-request`により分析と報告の対象から外す。
通読や候補一覧に残っている記録も、問題、再発防止の要求、見送り理由の説明とは区別して読む。
終了操作の語句を含んでも別の要求や方針変更を伴うときは、その趣旨どおり扱う。
この扱いは「一次選別」と`analysis-actions.md`「原因と対策」の報告にも適用する。

介入、WI記入欄の是正、ユーザー確認の必須性、除外区分の判定は、`../../user-confirmation-and-report/references/analysis-operations.md`「ユーザー介入と確認の判定」に従い、元の発話と構造化確認の全項目を判断材料にする。
介入として抽出された発話がシステムの自動生成テキストだった場合、人間の入力と誤分類した事象も共通分析へ送る。
自動生成テキストかの判定基準は、`agent-toolkit/agent_toolkit/_common/runtime_inserted.py`と`session-review-evidence --user-events`の抽出条件に従う。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 場面別の原因分析：情報探索の問題の基準を適用する前 | `agent-basics:bugfix/references/root-cause-scenarios-investigation.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-scenarios-investigation.md` |
| 振り返りの判断基準：原因と対策の基準を適用する前 | `analysis-actions.md` | 判断 | `read:agent-toolkit:*/skills/review-session/references/analysis-actions.md` |
| 振り返りの判断基準：比較して探す観点の基準を適用する前 | `analysis-cost.md` | 判断 | `read:agent-toolkit:*/skills/review-session/references/analysis-cost.md` |
