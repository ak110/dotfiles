# 読込の配送宣言

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 「確認要否の判定」を適用する前（Claude Codeで`AskUserQuestion`を呼ぶ前、Codexで構造化質問か`references/codex-format.md`の固定形式の質問を発行する前を含む。会話圧縮の後に同じ操作をする場合は読み直す） | `references/approval-scope.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/approval-scope.md` | |
| 確認本文と選択肢を起草する前（「確認の選択肢を組む手順」へ入る時点。`AskUserQuestion`の`questions`、Codexの構造化質問または固定形式の質問を組む前と、委譲先の返却が示した論点や問いの案から確認を組む前を含む。会話圧縮の後に同じ操作をする場合は読み直す） | `references/choice-construction.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/choice-construction.md` | |
| 協調モードで対象リポジトリの変更を伴う依頼へ着手する前 | `references/publish-scope.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/publish-scope.md` | |
| Codexで条件に合う構造化質問が無く、固定形式で質問を提示する時点 | `references/codex-format.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/codex-format.md` | |
| 起動元の工程が質問ラウンドを指定した時点 | `references/grilling.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/grilling.md` | |
| Claude Codeメインが利用上限の猶予通知を受けた時 | `references/usage-limit.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/usage-limit.md` | |
| 原要求を復元する資料を取得できず、未確定判断を保留または暫定する前 | `references/pending-decisions.md`の「未確定判断の保留と暫定判断」 | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/references/pending-decisions.md#「未確定判断の保留と暫定判断」` | |
| 回答を得られない確認の保留、無回答後の確認の記録、事前承認の合意判定、対話中のUWIへの回答の受領のいずれかへ進む前（メインエージェント） | `references/pending-decisions.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/pending-decisions.md` | |
| 回答済みUWIを元の作業へ反映する時（回答の到達を確認する時を含む）と、`atk run-skill`が起動したセッションで過去の実行のUWIを扱う時 | `references/answer-transitions.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/answer-transitions.md` | |
| 規範どうしが矛盾する場合 | `references/conflict-resolution.md`（由来確定） | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/conflict-resolution.md` | |
| 手順どおりに進められない場合 | `references/procedure-conflict.md`（判定順） | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/procedure-conflict.md` | |
| ツール呼び出しが権限設定またはauto mode classifierに拒否された場合と、委譲先の返却がその拒否を報告した場合 | `references/permission-denial.md`（拒否の確認手順、既知の誤拒否パターン、偽陽性と判断できる拒否への対応） | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/permission-denial.md` | |
| ユーザーへの確認・報告の手段を選ぶ時と認識合わせを行う時 | `references/communication-methods.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/communication-methods.md` | |
| 実際のユーザー発話を解釈・取り込み、訂正・割り込み・成果物の制限を扱う時 | `references/user-utterance.md` | 判断 | `read:agent-toolkit:*/skills/user-confirmation-and-report/references/user-utterance.md` | |
