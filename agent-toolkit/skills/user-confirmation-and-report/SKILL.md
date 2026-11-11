---
name: user-confirmation-and-report
user-invocable: false
description: >
  本スキルの内容が文脈に存在しない状態でユーザーの発話（自動生成されたプロンプトを除く）を受け取ったとき、
  応答を開始する前に必ず起動する。セッションの最初の発話や、会話のコンテキスト圧縮後に初めて受け取った発話がこれに該当する。
  計画作成・実装・提案・WI起草などで入力を「要件」または「技術判断」へ分類するときや、受け取った分類を採用・検収するときにも起動する。
  操作や判断について確認が必要かを判定するとき、ユーザーへの確認や報告の手段を選択するとき
  （Claude Codeの`AskUserQuestion`やCodexの構造化質問を呼び出す前を含む）、UWIへ確認事項を退避するとき、
  回答が得られたUWIを元の作業へ反映するとき、Claude Codeのメインエージェントが利用上限の猶予通知を受け取ったとき、
  あるいはツール呼び出しが実行基盤・権限設定・auto mode classifierによって拒否されたときや、classifierが判定不能を返したとき（委譲先からその報告を受けた場合を含む）にも起動する。
  ユーザーと自身の認識が食い違っている可能性があるときや、行動への指摘を繰り返し受けた際にも起動して認識合わせを行う。
---

# ユーザー確認とユーザーへの報告

確認の要否の判定、伝達手段の選択、回答後の作業再開までを扱う。
エージェントが判断できない事項だけをユーザーへ問い、技術判断は自ら確定して作業を進める。
UWIの本文、キューへの投入、状態管理、依存関係の形式は`agent-toolkit:wi-standards`を起動して従う。

対話の確認・説明にはホストの応答・質問手段を使う。
UWI保存とWI保留・再開にはキュー仕様と`atk`が要る。
CLIが無ければ確認本文と保存用原稿を返し、キューへ未保存と示す。
反映はこれらが使えるClaude CodeかCodexで引き継ぎ、原稿には[文章の品質基準](references/quality-standards.md)を適用する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| ユーザーから成果物の欠陥の指摘・割り込み・是正要求を受け取り、採否と修正範囲を確定する前 | `agent-toolkit:user-confirmation-and-report/references/review-feedback.md` |
| 状況照会で再検証の要否を判定する前 | `agent-basics:check-execution/references/verification-scope-reruns.md` |

## 読込表

条件付きの行の資料は、条件が成立した時点で該当する判断の前に読む。
取得済み本文の再利用と会話圧縮後の再読込は`agent-toolkit/share/rules/common/02-agent-operations.md`「ツール・コマンド運用」に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 「確認要否の判定」を適用する前（Claude Codeで`AskUserQuestion`を呼ぶ前、Codexで構造化質問か`references/codex-format.md`の固定形式の質問を発行する前を含む。会話圧縮の後に同じ操作をする場合は読み直す） | `references/approval-scope.md` |
| 確認前に技術的不確実性を実験で解消するか判断する前 | `agent-basics:writing-standards/references/design-heuristics-screening.md` |
| 確認本文と選択肢を起草する前（「確認の選択肢を組む手順」へ入る時点。`AskUserQuestion`の`questions`、Codexの構造化質問または固定形式の質問を組む前と、委譲先の返却が示した論点や問いの案から確認を組む前を含む。会話圧縮の後に同じ操作をする場合は読み直す） | `references/choice-construction.md` |
| 協調モードで対象リポジトリの変更を伴う依頼へ着手する前 | `references/publish-scope.md` |
| Codexで条件に合う構造化質問が無く、固定形式で質問を提示する時点 | `references/codex-format.md` |
| 起動元の工程が質問ラウンドを指定した時点 | `references/grilling.md` |
| Claude Codeメインが利用上限の猶予通知を受けた時 | `references/usage-limit.md` |
| 原要求を復元する資料を取得できず、未確定判断を保留または暫定する前 | `agent-toolkit:user-confirmation-and-report/references/pending-decisions.md` |
| 回答を得られない確認の保留、無回答後の確認の記録、事前承認の合意判定、対話中のUWIへの回答の受領のいずれかへ進む前（メインエージェント） | `references/pending-decisions.md` |
| 回答済みUWIを元の作業へ反映する時（回答の到達を確認する時を含む）と、`atk run-skill`が起動したセッションで過去の実行のUWIを扱う時 | `references/answer-transitions.md` |
| 規範どうしが矛盾する場合 | `references/conflict-resolution.md`（由来確定） |
| 手順どおりに進められない場合 | `references/procedure-conflict.md`（判定順） |
| ツール呼び出しが実行基盤・権限設定・auto mode classifierに拒否された場合、classifierが判定不能を返した場合、および委譲先からその報告を受けた場合 | `references/permission-denial.md` |
| ユーザーへの確認・報告の手段を選ぶ時と認識合わせを行う時 | `references/communication-methods.md` |
| 実際のユーザー発話を解釈・取り込み、訂正・割り込み・成果物の制限を扱う時 | `references/user-utterance.md` |
| 実ユーザー発話やWI記入欄の入力を受領・分類し、既存判断への介入かを評価する時 | `agent-toolkit:user-confirmation-and-report/references/analysis-operations.md` |

## 起動契機

起動条件はfrontmatterの`description`に従う。
除外する「自動生成されたプロンプト」は、システムがユーザー入力欄へ挿入し`atk-auto`タグで囲むメッセージ（`atk wi process-loop`が子セッションへ渡す初期プロンプトや、モデルの可用性チェック用プロンプトなど）である。
境界の識別とユーザー発話としての証拠性は`agent-toolkit/share/rules/common/01-agent.md`「方針が衝突する場合の優先順位」に従う。
委譲先のターンが受け取る委譲プロンプトもこの除外に含まれる。

不具合の有無・原因・修正案を回答、補足説明、確認案、委譲先やAWIへ渡す方針に含めるときは、診断の前に`agent-basics:bugfix`を起動する。
是正の認可の要否は`references/user-confirmation-and-report-authorization.md`「認可を要する操作」に従い、既存の仕様へ戻すバグ修正と新しい挙動の追加を区別する。

## 工程で参照する運用資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 成果物、報告・回答、委譲プロンプトへ書く事実主張を調査する時 | `agent-basics:writing-standards/references/investigation-sources.md`、`agent-basics:writing-standards/references/investigation-technical-clauses.md`、`agent-basics:writing-standards/references/investigation-results.md` |
| auto modeのカスタムルール編集（権限拒否に遭遇した場面は`agent-toolkit:user-confirmation-and-report`が扱う） | `agent-toolkit:user-confirmation-and-report/references/auto-mode.md` |

## 条件別資料

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 確認要否の判定・判定の細則に関する判断・作業の前 | `references/user-confirmation-and-report-decisions.md` |
| 認可を要する操作・原文からの具体化を要する軸に関する判断・作業の前 | `references/user-confirmation-and-report-authorization.md` |
| 確認を要する事項・確認の選択肢を組む手順に関する判断・作業の前 | `references/user-confirmation-and-report-questions.md` |
