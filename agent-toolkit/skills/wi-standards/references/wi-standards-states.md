# AWIとUWIの共通規範：状態と依存

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 報告用UWIを除くWIの意味確認で、この資料を選択して取得する時 | `draft-semantic-review.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/draft-semantic-review.md`、`wi-standard-save` | |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` | |
| 規範・基準・手順による既存の被覆を判定する前 | `agent-basics:writing-standards/references/agent-documents-basics-ownership.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/agent-documents-basics-ownership.md` | |
| run-skillの起動入力に独立WI受付があるとき | `../../../share/wi-submission.md` | 判断 | `read:agent-toolkit:*/share/wi-submission.md` | |
| 対象リポジトリとprivate-notesを扱う前 | `repository-scope.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/repository-scope.md` | |
| 委譲先が`atk wi`でキュー項目の状態を変更する場合 | `subagent-queue-ops.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/subagent-queue-ops.md` | |
| 通常AWIを起草する前 | `awi-body.md`、`awi-body-content.md`、`awi-body-evidence.md`、`awi-body-completion.md`、`awi-body-placement.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/awi-body.md`、`read:agent-toolkit:*/skills/wi-standards/references/awi-body-content.md`、`read:agent-toolkit:*/skills/wi-standards/references/awi-body-evidence.md`、`read:agent-toolkit:*/skills/wi-standards/references/awi-body-completion.md`、`read:agent-toolkit:*/skills/wi-standards/references/awi-body-placement.md` | |
| 通常AWIの要求を読解・採否判定する前 | `awi-body.md`、`awi-body-content.md`、`awi-body-completion.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/awi-body.md`、`read:agent-toolkit:*/skills/wi-standards/references/awi-body-content.md`、`read:agent-toolkit:*/skills/wi-standards/references/awi-body-completion.md` | |
| 観測された不具合を起点としbugfixを起動して起草する前 | `awi-body-evidence.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/awi-body-evidence.md` | |
| AWIの逐語引用を取り込む前 | `awi-body-quotation.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/awi-body-quotation.md` | |
| 外部の既存挙動の再現を完成条件へ採る前 | `awi-body-external.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/awi-body-external.md` | |
| 報告用UWIを除くWI作成の調査に入る前 | `drafting-workflow.md`、`drafting-workflow-evidence.md`、`drafting-workflow-investigation.md`、`drafting-workflow-requirements.md`、`drafting-completion-decisions.md`、`drafting-completion-scope.md`、`drafting-completion-confirmation.md`、`drafting-completion-policies.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-evidence.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-investigation.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-requirements.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-decisions.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-scope.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-confirmation.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-policies.md`、`bash:atk wi submission begin` | |
| 報告用UWIを除く原稿を確定する前 | `drafting-workflow-writing.md`、`drafting-workflow-validation.md`、`drafting-completion-validation.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-writing.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-validation.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-validation.md`、`wi-standard-save` | |
| 報告用UWIを除くWIの独立した確認に入る前 | `drafting-workflow-review-route.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-review-route.md`、`wi-standard-save` | |
| 報告用UWIを除くWIの保存操作に入る前 | `drafting-workflow-saving.md`、`drafting-completion-duplicates.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-workflow-saving.md`、`read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-duplicates.md`、`wi-standard-save` | |
| 報告用UWIを除く投入済みWIへ着手する前の対象集合と除外の確認 | `drafting-completion-validation.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/drafting-completion-validation.md` | |
| report-completionが定義する報告用UWIの原稿確定・意味確認・保存に入る前 | `../../report-completion/references/report-formats-uwi.md`、`uwi-format.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/references/report-formats-uwi.md`、`read:agent-toolkit:*/skills/wi-standards/references/uwi-format.md` | |
| UWIを起草する場合 | `uwi-format.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/uwi-format.md` | |
| WIの調査・要件分類・由来の検収に入る前 | `origin-and-approval-origin.md`、`origin-and-approval-comments.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/origin-and-approval-origin.md`、`read:agent-toolkit:*/skills/wi-standards/references/origin-and-approval-comments.md` | |
| 逐語引用を扱う前 | `origin-and-approval-quotation.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/origin-and-approval-quotation.md` | |
| WIの状態、状態遷移、依存、冷却時間、保留と終了を扱う前。既存WIの更新・修復・採否の見直しが必要と判明した時点や、既存WIに対する具体的な懸念を受けた時点（調査や認識合わせより先） | `states-and-dependencies.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/states-and-dependencies.md` | |
| 複数リポジトリへ投入する場合、および同じ成果の整合を要求する複数のAWIを登録する場合 | `cross-repository-submission.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/cross-repository-submission.md` | |
| 同じ対象リポジトリの複数項目を一括取得する場合 | `wi-show-bulk.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/wi-show-bulk.md` | |
| 通常AWIの投入、または投入済みAWIの内容の更新を、WI投入担当へ委譲するか自分で起草するかを決める前 | `../../../share/add-wi-parent-launch.md` | 判断 | `read:agent-toolkit:*/share/add-wi-parent-launch.md` | |
| WI投入担当を起動する前（委譲すると決めた後） | `${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`。起動・受領の契約として全文を読む | 判断 | `read:agent-toolkit:*/share/add-wi.parent.md` | |
| AWIの原因分析を起草する前、計画ファイル（バグ）を作成する前 | `agent-toolkit:wi-standards/references/analysis-records.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/analysis-records.md` | |
| エージェント向け文書へ新しい規定を追記する時と、文書の記述量を管理する時 | `agent-toolkit:wi-standards/references/agent-documents-additions.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/agent-documents-additions.md` | |

## 状態と依存

WIの保存先は`private-notes/yyyy/MM/<filename>`に固定する。
項目の種別と保存状態は、frontmatterの`type`および`state`で管理する。
通常の本文取得には`atk wi show <WIファイル名>`を使用する。
手動で物理パスを組み立てず、保存先の解決は共通の配置解決機能に任せる。
状態遷移した後も、ファイルパス、内部キー、画面URLは同じ値を維持する。
旧配置からの移行は公開CLIの移行機能に任せ、配置の競合が発生した場合は診断メッセージに従って解消してから再実行する。

WIの保存状態には`inbox`、`processing`、`hold`、`adopted`、`rejected`があり、`agent-toolkit:process-wi`は`inbox`と`processing`のみを処理対象とする。
既存WIの更新・修復・採否の見直しが必要と判明した時点や、既存WIに対する具体的な懸念が寄せられた時点では、詳細な調査や認識合わせに先立って対象項目を保留（`hold`）にする。
処理中の項目の扱い: 別のセッションが処理している`processing`状態の項目については、保留や本文の置き換えを行わず、変更したい内容を新規項目として投入する。
状態遷移、採否の操作、保留と本文の修正、終了処理、依存関係と冷却時間、事前承認型UWIを待つ元項目の依存、および「回答済みUWIの取り込み」の運用は[references/states-and-dependencies.md](states-and-dependencies.md)が定める。
上流リポジトリへの投入手順は[references/cross-repository-submission.md](cross-repository-submission.md)が定める。

重要フラグはfrontmatterの`important: true`で設定し、キーの省略時はfalse、フラグの解除はキー自体の削除として扱う。
一覧表示、検索、全件表示およびWeb画面の一覧では、`all`、`yes`、`no`の重要度フィルターを利用できる。
重要フラグは、通常のprocess-wiにおける選定や優先度、状態遷移には影響せず、ユーザーが対象集合として明示的に指定した場合に参照する。
