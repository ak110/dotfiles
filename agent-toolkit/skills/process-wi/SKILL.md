---
name: process-wi
description: >
  対象リポジトリのAWIを取得し、レーンへ分けて計画、実装、レビュー、統合および公開を完遂するときに起動する。
---

# AWIのレーン処理

選定時に固定したAWIをレーンへ分け、各レーンの同じ担当threadが計画（要る場合）から統合までを担う。メインは選定、判断が要る計画境界の確認、実行レビューの調整、公開工程およびセッション終端を担う。
AWIとUWIの共通契約は`../wi-standards/SKILL.md`、確認要否と確認手順は`agent-toolkit:user-confirmation-and-report`に従う。本スキルの実行中は自律モードとする。

## 読込表

次の時点または条件が成立したら、その操作の前に同じ行の資料を全文読む。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| メインが対象選定を開始する前（実行順2）と、処理対象WIを追加する前 | `references/selection-procedure.md` |
| 選定YAMLを生成または消費する前 | `references/selection-format.md` |
| 専用worktreeとレーンを作成する前（実行順3） | `references/run-lanes.md` |
| レーン担当を起動する前 | `${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md` |
| 不良またはユーザーの是正要求を受け取った時 | `references/immediate-fix.md` |
| 公開工程を開始する前（実行順7） | `references/finish-session.md` |
| 終端担当を起動する前 | `${CLAUDE_PLUGIN_ROOT}/share/session-termination.parent.md` |

## 用語

WI作成、計画、実行および実行レビューの責務と受渡しは`${CLAUDE_PLUGIN_ROOT}/share/workflow-phases.md`に従う。

- **メイン**: `agent-toolkit/rules/01-agent.md`が定めるメインエージェントの短縮呼称
- **選定工程、レーン工程、公開工程**: 本スキルの3つの主要工程。詳細はそれぞれ`references/selection-procedure.md`、`references/run-lanes.md`、`references/finish-session.md`が定める。公開工程の定義は`agent-toolkit:commit`の`references/publish.md`冒頭にあり、本スキルでは終端担当が担う
- **処理対象WI**: メインが選定時に固定した、1回の実行で処理するAWI。処理中にユーザーが追加を明示したAWIを含む
- **レーン**: 処理対象を割り当てる処理単位。専用branchと専用worktree（レーンへ1つ割り当てるgit worktree）を1つずつ持つ
- **レーン担当**: 各レーンの計画、実装、レビュー修正、履歴統合および主作業ツリーへの統合を同じthreadで担う担当。担当種別はレーン担当、レビュー修正担当、CI修正担当の3種とする（`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`の操作区分）
- **主作業ツリー**: マージ先branchをチェックアウトしている作業ツリー。`atk wi process-loop`が起動したセッションでは、process-loopが用意して子セッションを起動したworktree（起動時の作業ディレクトリ）を指す
- **終端担当**: 公開工程（push、CI、検証失敗時の修正、プロジェクト固有の公開後の操作、延期adoptなど）を担う委譲先。委譲範囲は`references/finish-session.md`「終端担当の起動と並行工程」が定める
- **即時対応**: 公開工程の開始前に見つかった不良と処理中のユーザーの是正要求を、「即時対応」の基準で判定し、同じセッションで類似見直しと再発防止策まで是正する手順

## 不変条件

- 計画ファイルの作成要否は`agent-toolkit:plan-mode`「計画ファイルの作成要否」で判定する。作成するレーンは実装前に起草し、処理中に追加したAWIは凍結済み計画と分けて追加計画へ置く。作成を省くレーンはWIの要求と完成条件を実装とレビューの基準とする
- 計画のレビュー工程は置かず、実装後に計画ごとに最終実装HEADで要件・外部仕様水準の実行レビューを行う
- レーン担当は専用worktreeの書込を所有し、主作業ツリーへ書き込むのは統合のときに限る。担当範囲の一部を委譲先へ分ける場合は`agent-toolkit:delegation`の`references/mandatory-rules.md`「作業ツリーの保護」に従い、同じ専用worktreeの分担と別branch・別worktreeの内部群を使い分ける。計画、検収、commitの対応記録、統合とメインへの返却はレーン担当が担う
- 人間由来の要求を全部または一部不採用にする場合は、ユーザーの確認またはUWIの回答を得てからその項目を終端する
- 選定時に固定した処理対象WIを最後まで使い、ready一覧を再取得しない。例外として加える項目は、ユーザーの明示追加、処理中に告げられた回答済みUWIとその元項目、即時対応で確定したAWIとする。その検収と割当は`references/selection-procedure.md`「処理対象WIの追加」と`references/run-lanes.md`「処理中に確定した必須是正レーン」に従う。元の選定結果へ追記してからレーンへ渡す
- 対象リポジトリへpushするのは公開工程の終端担当だけとする。前回の公開後に同じセッションで新しい成果がベースbranchへ統合された場合は、追加差分を入力に終端担当を再起動し、セッション終了前に最新HEADのpushとCIを検収する（`references/finish-session.md`「公開状態の検収と再起動」）
- 計画とレビュー指摘管理表は、それを使う全工程の完了後にだけ回収する。レーンの専用branch・worktree・個別managed-tempは統合後も保持し、全工程・外部プロセスの終了後に所有登録とGit状態を再判定して終了時回収する
- メインの作業ディレクトリが実行ホストの隔離worktree（Claude Codeの`EnterWorktree`など）の内側にある場合は、隔離を解除してから進む。専用worktreeの作成と回収が対象リポジトリのGit共通ディレクトリを書き換えるためである。`atk wi process-loop`が用意したworktreeは主作業ツリーであり、解除しない

## 実行順

1. `## 直前の同期結果の検分`に従い、このセッションでAWIの処理を完遂できるかを判定する。
2. `references/selection-procedure.md`に従い、メイン自身が対象選定と集合確定直後の処理開始を行う。意味判断と取得済み候補をpick-wi-checkへ渡し、定型欄の生成・確認・保存を1回で行う。選定を別の担当へ委譲しない。あわせて`## 自動コードレビュー監査`を行う。
3. `references/run-lanes.md`に従い、選定結果の段階と先行レーンから`atk lane create`へ対象を渡し、準備済み資源を受領してレーン担当を起動する。後段は先行レーンの統合と書込主体の解放を受領してから現行HEADを基点に作成する。Claude Codeでは各段階の全初回起動後に共通の圧縮予約手順へ進む。
4. 各レーンの計画または計画なしの準備結果を`${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`「計画準備の受領」に従って受け取り、判断を要する場合だけ`実装開始`または`実装なし`を返す。
5. `実装完了`と検証結果を受領したレーンごとに、`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`と`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`に従って実行レビューを収束させる。
6. 追加した処理対象WIも`references/run-lanes.md`に従って割り当てて収束させる。同じレーン担当threadへ`${CLAUDE_PLUGIN_ROOT}/share/exec.parent.md`「統合の指示と受領」に従って統合を指示し、計画最終化とAWI終端までを完了させる。
7. 全レーンの終端と起動した監査の処置確定の後、`references/finish-session.md`に従って公開とセッション終端を完遂する。

## 直前の同期結果の検分

同期が失敗したまま着手すると、AWIの処理が途中で成立しなくなることがある。選定の前に`atk config show`が示す`state_dir`直下の`sync-report.json`を読む。

- ファイルが無いか`status`が`succeeded`なら、そのまま次の工程へ進む
- `status`が`failed`か`post_apply`の`failed_steps`が1件以上なら、失敗の内容からこのセッションで処理を完遂できるかを判定する。できる場合は判定と根拠を報告して進む。できない場合は着手せず`atk wi process-loop abort`でprocess-loopへ中断を要求し、失敗した段と根拠を報告してセッションを終える

## 自動コードレビュー監査

自動コードレビュー監査とは、選定と並行してGitHub CopilotのレビューとDependabotアラートを取得し、必要な処置を確定する工程を指す。監査担当は対象リポジトリの成果物を読み取りだけで扱う。

選定の開始前に、対象がGitHub上にあれば`atk review-audit pending --repo <OWNER>/<REPO>`を実行する。終了コード0で`counts.reviews`、`counts.threads`、`counts.dependabot`がいずれも0なら監査担当を起動せず、3件数を監査省略の根拠として完了報告へ渡す。それ以外（1件以上、非0終了、JSONや件数を解釈できない場合）は`${CLAUDE_PLUGIN_ROOT}/share/copilot-review-audit.parent.md`に従って監査担当を起動し、選定と並行させる。`dependabot.status`が`disabled`か`unauthorized`なら、その状態を完了報告へ渡す。監査の処置と公開工程の開始の関係は`references/finish-session.md`冒頭が定め、新しいレビューの到着は次の実行の監査で扱う。

`atk wi process-loop`はキューが空でも未判定のDependabotアラートがあればAWIを起票せずにprocess-wiを起動する。このときの候補0件の選定結果は正常として保存し、レーンを起動せずに監査の処置を確定して、`references/finish-session.md`「公開対象が無い場合の短絡」を経て終端する。要修正を同じセッションで直す場合は「即時対応」に従う。

## 同一主題の追加指示

本スキルの起動中にユーザーが追加した指示は、既存の処理条件と比べて採否を決めた差分だけを処理条件へ反映する。比較と採否は`agent-toolkit:review-standards`の`references/reviewee.md`「指摘発生時の扱い」、範囲と論理関係の解釈は`agent-toolkit:user-confirmation-and-report`の`SKILL.md`「原文の保持と取り込み」に従う。公開工程の開始前に届いた直接実装を求める表現も、同一主題である間は本スキルの処理を続ける追加条件として扱う。公開工程の開始後の指示は`references/immediate-fix.md`「対象外の不良と是正要求」に従う。主題の継続は原要求・現物・既存認可から自ら判定し、ユーザーだけが決められる目的・範囲・認可が残る場合だけ変更前に確認する。

追加指示が既存の要件を変える場合は、変更前の要件で作業ツリーの外へ生成済みの成果物（キュー項目、Issue・MRの本文など）を確かめ、終端・改訂・取り消しなどの処置を決めて同じ追送へ含める。技術と既存認可で決まる処置は自ら確定して報告する。作業ツリーに依存しない外部成果物の編集はメインが中継と同時に行い、作業ツリー内の差分はレーン担当が計画と実装で追随させる。

追送には`agent-toolkit:delegation`の`references/base-contract.md`の入力の適格性を適用し、採用した要件差分と外部成果物の処置だけを渡す。commitの分け方、検証、pushなどレーン担当の規範が決める手段は加えない。

## 即時対応

延期を判断する前に、不良が本セッションの残りの工程へ作用するかを判定する。本セッションの作業で導入した不良は同じセッションで扱う。本セッションで扱った問題と根本原因を同じくする不良は独立した既存不良とせず、その問題の類似見直しに含める（範囲・時機・完了は`agent-toolkit:bugfix`の`references/root-cause-analysis.md`「QとC・Dの区別」「類似見直しと再発防止の完了」に従う）。既存不良は、残工程を停止・遮断する場合、不良のある判断入力を使うことで要求・判断・成果物を誤る場合、および類似見直しの範囲を狭める規範欠陥のように残る振り返りの判断へ作用する場合に即時対応する。残工程へ作用せず根本原因も異なる独立した不良・改善は、持ち越すと次のセッションの選定・レーン・公開が停止・遮断されるか作業のやり直しになる場合だけ即時対応し、それ以外は次回以降へ回す。呼称・表記・誤記や成果物の品質という種類だけで対象外にせず、経過時間、実装規模、検証の作業量でも判定しない。ユーザーが時機を明示した場合はその指示に従う。

手順、レーンの変更範囲へ修正を加える認可、および対象外とした不良と是正要求の扱いは`references/immediate-fix.md`が定める。

## 終端

工程境界そのものを確認理由にせず、確認要否の判定で必要な判断だけを確認待ちとして、依存しない工程を続ける。回答を得られない確認は`agent-toolkit:user-confirmation-and-report`の手順でUWIへ退避し、`processing`の元項目を`atk wi hold --state=processing <元項目のファイル名>`で保留する。
完了報告は`agent-toolkit:completion-report`に従い、本スキルの工程で生じたcommitを公開してから報告する。

### 終端工程の承認スコープ

環境変数`AGENT_TOOLKIT_PROCESS_LOOP_SESSION`が`1`の起動を自動常駐起動（`atk wi process-loop`が子セッションへ設定する）、それ以外を手動起動とする。判別は選定工程で1回行う。

手動起動では、固定したAWIの完成条件に明示されたrelease、tag、push、PRもしくはMRまたは配布を、記載された操作、対象および影響範囲に限って承認スコープとして保持する。自動常駐起動では保持しない。承認済み範囲はそのまま用い、新しく具体化して範囲が変わる部分だけをユーザー確認する。破壊的操作の事前説明は維持する。
