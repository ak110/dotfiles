# ユーザー確認とユーザーへの報告：確認を要する事項

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` | |
| ユーザーから成果物の欠陥の指摘・割り込み・是正要求を受け取り、採否と修正範囲を確定する前 | `agent-toolkit:user-confirmation-and-report/references/review-feedback.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/review-feedback.md` | |
| 状況照会で再検証の要否を判定する前 | `agent-basics:check-execution/references/verification-scope-reruns.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/verification-scope-reruns.md` | |
| 「確認要否の判定」を適用する前（Claude Codeで`AskUserQuestion`を呼ぶ前、Codexで構造化質問か`codex-format.md`の固定形式の質問を発行する前を含む。会話圧縮の後に同じ操作をする場合は読み直す） | `approval-scope.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/approval-scope.md` | |
| 確認前に技術的不確実性を実験で解消するか判断する前 | `agent-basics:writing-standards/references/design-heuristics-screening.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/design-heuristics-screening.md` | |
| 確認本文と選択肢を起草する前（「確認の選択肢を組む手順」へ入る時点。`AskUserQuestion`の`questions`、Codexの構造化質問または固定形式の質問を組む前と、委譲先の返却が示した論点や問いの案から確認を組む前を含む。会話圧縮の後に同じ操作をする場合は読み直す） | `choice-construction.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/choice-construction.md` | |
| 協調モードで対象リポジトリの変更を伴う依頼へ着手する前 | `publish-scope.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/publish-scope.md` | |
| Codexで条件に合う構造化質問が無く、固定形式で質問を提示する時点 | `codex-format.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/codex-format.md` | |
| 起動元の工程が質問ラウンドを指定した時点 | `grilling.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/grilling.md` | |
| Claude Codeメインが利用上限の猶予通知を受けた時 | `usage-limit.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/usage-limit.md` | |
| 原要求を復元する資料を取得できず、未確定判断を保留または暫定する前 | `agent-toolkit:user-confirmation-and-report/references/pending-decisions.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/pending-decisions.md` | |
| 回答を得られない確認の保留、無回答後の確認の記録、事前承認の合意判定、対話中のUWIへの回答の受領のいずれかへ進む前（メインエージェント） | `pending-decisions.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/pending-decisions.md` | |
| 回答済みUWIを元の作業へ反映する時（回答の到達を確認する時を含む）と、`atk run-skill`が起動したセッションで過去の実行のUWIを扱う時 | `answer-transitions.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/answer-transitions.md` | |
| 規範どうしが矛盾する場合 | `conflict-resolution.md`（由来確定） | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/conflict-resolution.md` | |
| 手順どおりに進められない場合 | `procedure-conflict.md`（判定順） | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/procedure-conflict.md` | |
| ツール呼び出しが実行基盤・権限設定・auto mode classifierに拒否された場合、classifierが判定不能を返した場合、および委譲先からその報告を受けた場合 | `permission-denial.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/permission-denial.md` | |
| ユーザーへの確認・報告の手段を選ぶ時と認識合わせを行う時 | `communication-methods.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/communication-methods.md` | |
| 実際のユーザー発話を解釈・取り込み、訂正・割り込み・成果物の制限を扱う時 | `user-utterance.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/user-utterance.md` | |
| 実ユーザー発話やWI記入欄の入力を受領・分類し、既存判断への介入かを評価する時 | `agent-toolkit:user-confirmation-and-report/references/analysis-operations.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/analysis-operations.md` | |
| 成果物、報告・回答、委譲プロンプトへ書く事実主張を調査する時 | `agent-basics:writing-standards/references/investigation-sources.md`、`agent-basics:writing-standards/references/investigation-technical-clauses.md`、`agent-basics:writing-standards/references/investigation-results.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/investigation-sources.md`、`read:agent-basics:*/skills/writing-standards/references/investigation-technical-clauses.md`、`read:agent-basics:*/skills/writing-standards/references/investigation-results.md` | |
| auto modeのカスタムルール編集（権限拒否に遭遇した場面は`agent-toolkit:user-confirmation-and-report`が扱う） | `agent-toolkit:user-confirmation-and-report/references/auto-mode.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/auto-mode.md` | |

## 確認を要する事項

`user-confirmation-and-report-decisions.md`「確認要否の判定」で確認する代表的な場面を挙げる。
確認文にはユーザー要件や運用方針の未確定部分だけを書き、技術的選択肢や調査の途中経過は書かない。
手段は`communication-methods-delivery.md`「手段の選択」に従う。

- 原文・過去の発話・依頼の目的を調べても解釈を1つに絞れず、解釈によってエンドユーザーから見える結果が変わる場合
- 単独の発話から工程を特定できず、直前の要求との組み合わせ方で工程が分かれる場合（前後関係を確定してから変更や委譲へ進む）
- 意図しない外部公開を解消した後の復旧で、追加の認可や復旧方針の選択が要る場合（解消の事実・影響・復旧方針は直ちに報告し、既存認可内の復旧は自ら行う。作業中に見つけた認証情報は含めず、`agent-toolkit/share/rules/common/01-agent.md`「完遂と先送り」に従う）
- WIの完成条件に含まれない不足のうち、`user-confirmation-and-report-authorization.md`「認可を要する操作」で「新しい挙動の追加」と判定したものを今回の作業に含めるか別作業にするかを決める場合

最後の項目は公開工程の最中でも直ちに確認し、回答を待たずに確定できる工程は続ける。
確認文には、観測した停止場面、今回の変更でその問題に遭いやすくなるか、別作業にした場合のリリース内容の差を書く。
「今回の作業に含める」と回答されたら実行中のスキル（`agent-toolkit:process-wi`など）の追加指示や即時対応の手順で同じ実行サイクルへ組み込み、公開工程の開始後の回答は同スキルの公開後指示に従う。
期限までに回答が無ければ`pending-decisions.md`「未確定判断の保留と暫定判断」に従って実装を見送り、WI投入担当へAWIの起草を委譲し、実施の可否を問う事前承認型UWIを記録して、そのAWIの`depends_on`へUWIを加える。
認可済みの元の変更は通常どおり公開・リリースの判定へ進める。

## 確認の選択肢を組む手順

現在求められている回答と、それに依存する確認の順序は`user-utterance-questions.md`「問い・提案・疑問形の扱い」に従う。
質問の前提も原文の保持と取り込みの対象とし、原文・回答とエージェントの案を区別する。

確認は検証済みの事実を前提に、質問文だけで判断でき、合意がそのまま成果物に反映されるように組む。
起草は`choice-construction.md`の「根拠の確定」「候補の構成」「影響と回答単位」「本文の語」に従う。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 確認・報告の手段と認識合わせ：手段の選択の基準を適用する前 | `communication-methods-delivery.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/communication-methods-delivery.md` |
| ユーザー発話の解釈と取り込み：問い・提案・疑問形の扱いの基準を適用する前 | `user-utterance-questions.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/user-utterance-questions.md` |
| ユーザー確認とユーザーへの報告：認可を要する操作の基準を適用する前 | `user-confirmation-and-report-authorization.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/user-confirmation-and-report-authorization.md` |
| ユーザー確認とユーザーへの報告：確認要否の判定の基準を適用する前 | `user-confirmation-and-report-decisions.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/user-confirmation-and-report-decisions.md` |
