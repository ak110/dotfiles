# 確認・報告の手段と認識合わせ

共通の確認要否・認可の判定は`../SKILL.md`に従う。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文の参照基準を適用する前 | `agent-basics:bugfix/references/root-cause-analysis.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-analysis.md` |
| 本文の参照基準を適用する前 | `agent-basics:bugfix/references/root-cause-remediation.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-remediation.md` |
| 本文の参照基準を適用する前 | `../../review-standards/references/reviewee.md` | 判断 | `read:agent-toolkit:*/skills/review-standards/references/reviewee.md` |
| 本文の参照基準を適用する前 | `approval-scope.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/approval-scope.md` |
| 本文の参照基準を適用する前 | `pending-decisions.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/pending-decisions.md` |
| 本文の参照基準を適用する前 | `../../wi-standards/references/uwi-format.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/uwi-format.md` |
| 本文の参照基準を適用する前 | `permission-denial.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/permission-denial.md` |
| 本資料の基準で判断する前 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md` |

## 手段の選択

工程の定義は`agent-toolkit/rules/01-agent.md`「役割分担」に従い、後述の個別UWIの条件と、権限拒否の偽陽性に対する`permission-denial.md`の手順は下表より優先する。表の「発話本文」はユーザーへのメッセージである。Claude Codeでは`agent-toolkit/share/rules-main.claude-code.md`「ツールの入出力」に従って`mcp__agent-toolkit__send_to_user`で届ける。Codexでは途中経過を`commentary`、通常の最終応答を必要なツール処理後の`final`で届ける。成果作業ではcompletion-reportで確定した作業完了報告の本文をそのまま用いる。ホスト本体を停止する場合、必要な可視報告は停止前の`commentary`で届ける。自律モードの伝達は報告用UWIの保存検収によって完了する。報告の送達を終えてからホストを停止する。Codexの命令や公開機能は`agent-toolkit/share/rules-common.codex.md`「Codexホスト契約の適用」に従う。

| 工程 | 協調モード | 自律モード |
| --- | --- | --- |
| 事前承認 | 構造化質問を使う。Claude Codeでは`AskUserQuestion`と`rules-main.claude-code.md`の制約、Codexでは`rules-main.codex.md`の手順に従い、合う手段が無ければ`codex-format.md`の固定形式を使う | 実際に働く回答期限があり、ホスト契約に合う構造化質問を発行できるメインエージェントだけがその場で質問する。期限を過ぎたら`pending-decisions.md`「未確定判断の保留と暫定判断」へ進む。それ以外は最初から事前承認型UWIへ記録し、回答を要する元項目だけを保留する |
| 事前承認以外で回答を待つ通常の確認 | 事前承認と同じ | 回答期限によらずその場では質問せず、事前承認型UWIへ記録して`pending-decisions.md`「未確定判断の保留と暫定判断」に従う |
| 事後承認 | その場で構造化質問する | 回答を待たずに事後承認型UWIへ記録し、作業を続ける |
| 認識合わせ | 「認識合わせ」の手順と事前承認の手段でその場で質問し、回答を待つ | 原則としてその場で質問する。有効な回答期限を過ぎたら`pending-decisions.md`「未確定判断の保留と暫定判断」へ進み、期限が無ければ回答を待つ。構造化質問を発行できないCodex Default modeでは`rules-main.codex.md`の例外に従い、自分の理解と質問をUWIへ記録して、回答を要する元項目だけを保留する |
| ユーザーへの報告 | 発話本文として届ける。作業完了時は`agent-toolkit:completion-report`のフォーマットを使う | 同スキルの報告用UWIに記録する（セッションにつき1件） |

表の`rules-main.claude-code.md`と`rules-main.codex.md`は`agent-toolkit/share/`配下を指す。
自律モードの「有効な回答期限」は、`atk wi process-loop`のように起動時に与えられた有限の期限を指す。無期限の設定、`askUserQuestionTimeout`などの期限機能が無い環境、期限が無効になる条件が成立している環境、Codex Default modeは「期限なし」とする。認識合わせを期限によらずその場で問うのは、割り込みや是正の直後に理解を確かめるためである。
回答が無いまま終わった確認と事前承認を要する元項目の保留は`pending-decisions.md`に従う。ユーザーとの窓口を持たない委譲先は確認通知で委譲元へ返す（`agent-toolkit/share/rules-subagent.md`「確認事項の即時通知」）。

個別UWIの要否は`../SKILL.md`「確認要否の判定」に従う。依頼されたバグ修正の結果、自明な取得や再実行の案内、環境の制約で実機確認できなかった範囲は通常の完了報告に含める。手順が定める報告用UWIは必ず保存し、ユーザーが結果を確認できるようにする。

次の場合はモードによらず個別UWIを使う。

- AWIの`## ユーザーコメント`やUWIの`## 回答`へ直接書かれた質問への回答: 個別UWIで報告する。自律モードの報告用UWIへの記載（`completion-report`に従う）で代えない。
- ユーザーの判断・資料・操作が次の作業に要る未完了事項: 必要と確定した時点で個別UWIへ保存し、成功を確かめてから所在を案内する（同じ依頼の既存UWIは再利用する）。回答を待たずに確定できる作業は続け、関連作業の完了後に要る事項はその完了工程でのUWI登録を記録する。会話中の確認で回答を得た事項はUWIの対象外とする。
- 作業中に見つけた認証情報: `agent-toolkit/rules/01-agent.md`「完遂と先送り」に従い、所在ごとに報告用UWIを1件登録する。所在は`agent-toolkit:wi-standards`の`references/uwi-format.md`の形式に揃え、同じファイルの値は1件にまとめる。投入前に所在を固定文字列として`atk wi grep --summary-only -F --type=uwi --state=all --target-repo=all -- '<所在>'`で探し、同じ所在の報告があれば発見手法・作業リポジトリ・採否・回答状況が違っても重ねない。無ければ発見時の作業リポジトリに`--question-type=post-approval`でUWIを登録し、報告だけで扱った対応の是非を問う。

報告にはUWIの所在だけを書き、判断や行動を求める依頼見出しは省く。

自律モードでは次を個別の事後承認にせず報告用UWIに含める。事前承認の対象は事前承認のままとする。

- 起動中のスキル・適用中の規範・指示・計画の回答が定めた工程、対象範囲、副作用の枠内の作業
- 調査で解釈が一意に決まった要求の実施
- 規範からの逸脱（`approval-scope.md`）
- エージェントが起草した完成条件の技術的な理由による変更
- 限定探索の終了や再発防止策を見送る判断。`agent-basics:bugfix/references/root-cause-analysis.md`「QとC・Dの区別」と、`agent-basics:bugfix/references/root-cause-remediation.md`「再発防止策の必須性」で重大度に応じて確定する。`session-review`の対象では振り返り結果の報告が兼ねる

事後承認は、実施の是非がユーザー固有の基準（選好・認可・成果物の見え方など）で決まる場合だけ求め、回答で分かれる観測結果や操作を書けなければ報告に回す。判断材料を内部の工程や進捗でしか説明できない事項も報告に回す。報告は判断を求める行為と区別する。報告用UWIが事後承認型の2択フォーマットなのは、是正を書き戻せる回答欄を設けるためである。
非同期質問の送信成功や初期カーソル位置は回答や承認ではなく、回答を要する操作は実際の回答まで確定しない。確認の拒否や承認要求の取り下げは無回答と区別し、`../SKILL.md`「確認要否の判定」を再適用する。認識の違いで要件や結果が変わる事項が残れば元項目を保留（hold）したまま事前承認型UWIへ記録し、確認が不要なら自ら確定して続ける。

## 認識合わせ

ユーザーと認識がずれている可能性があるとき、行動を繰り返し指摘されたとき、ユーザー発話の全角`！！`を契機にUserPromptSubmitフックの注記を受けたときは、ユーザーの目標と課題の理解を次の手順で確かめる。

1. ユーザーが達成したい目標と解決したい課題を自分の言葉で示し、発話から得た理解と自分が補った前提を区別する。
2. 発話・記録・現物から確定できる訂正はその場で直して報告する。残る相違だけを`../SKILL.md`「確認要否の判定」と`../SKILL.md`「確認の選択肢を組む手順」で問いにし、「手段の選択」の認識合わせの手段で実際の回答を得る。理解の確認と解決案の承認は別の問いにする。
3. 回答を元の依頼の目的・要件と照らして次の対応（採用済み案の縮小・撤回・差し替えを含む）を決める。判断は`agent-toolkit/share/rules-main.md`「目的との一致の確認」と`agent-toolkit:review-standards`の`references/reviewee.md`「ユーザー指摘の受領」に従う。

関係しない工程の実行モードは保つ（`agent-toolkit/share/rules-main.md`「協調と自律」）。委譲先で認識合わせが要る場合は、確認すべき論点を委譲元へ返す。
