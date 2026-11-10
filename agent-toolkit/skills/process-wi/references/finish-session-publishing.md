# 公開工程とセッション終了：公開対象が無い場合の短絡

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 合流した公開対象を判断する前 | `selection-procedure-addition.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/selection-procedure-addition.md` |
| 目標起点の公開対象を判断する前 | `lanes-goals.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/lanes-goals.md` |
| 未達条件の延期とユーザー確認・冷却を選ぶ前 | `../../wi-standards/references/states-and-dependencies-dependencies.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/states-and-dependencies-dependencies.md` |
| 基準資料を選択・取得する時 | `agent-toolkit:commit/references/push-and-ci.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/push-and-ci.md` |
| 基準資料を選択・取得する時 | `agent-toolkit:/share/session-termination.parent.md` | 判断 | `read:agent-toolkit:*/share/session-termination.parent.md` |
| 基準資料を選択・取得する時 | `../../commit/references/history-rewrite-wi-records.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/history-rewrite-wi-records.md` |
| 基準資料を選択・取得する時 | `agent-toolkit:commit/references/publish.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/publish.md` |
| 基準資料を選択・取得する時 | `../../../share/add-wi.parent.md` | 判断 | `read:agent-toolkit:*/share/add-wi.parent.md` |
| 基準資料を選択・取得する時 | `agent-toolkit:report-completion/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/SKILL.md` |
| 基準資料を選択・取得する時 | `agent-toolkit:delegation/references/waiting-claude-code.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/waiting-claude-code.md` |
| 統合後の即時修正の担当と時機を決める前 | `agent-toolkit:process-wi/references/immediate-fix.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/immediate-fix.md` |
| 公開後にも独立して成立するWI投入を別実行へ引き継ぐ前 | `../../../share/wi-submission.md` | 判断 | `read:agent-toolkit:*/share/wi-submission.md` |
| 基準資料を選択・取得する時 | `../../commit/references/push-and-ci.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/push-and-ci.md` |
| 基準資料を選択・取得する時 | `../../report-completion/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/report-completion/SKILL.md` |
| 公開版反映後の役割起動で稼働実装と資源の版相違が示された時 | `../../delegation/references/wait-anomalies-failures.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/wait-anomalies-failures.md` |

## 公開対象が無い場合の短絡

公開の短絡・直接実行・委譲を選ぶ前に、選定メインは保存した選定原本へ次を実行する。

```text
atk run-script pick-wi-check -- <選定YAMLの絶対パス> --require-integrated
```

終了0は全段階のレーンの統合済みを示す。
未統合を示す終了1では、出力されたレーン・段階・所属WI・先行レーンが原本と一致することを確かめ、未起動の後続段階も開始・継続する。
既存の依存・確認・冷却の基準で延期すると判断したWIは、理由・解除条件・再開位置を既存記録へ対応付け、今回の選定全体を処置した根拠を保持する。
延期を表すために統合状態を書き換えない。
終了2の入力不備を直し、原本との一致確認と意味判断を終えてから公開へ進む。
全体の母集団には選定原本の全対象を用い、レーンなしのWI、上流投入、回答済みUWIの処置も別途検収する。
選定原本の所在とこの判断の記録は、公開担当とall-reposの全体結果へ引き継ぐ。

**1.** 公開対象となる変更や外部操作が1つも存在せず、かつ公開状態の4項目がすべて成立している場合に限り、公開担当を起動せずに`finish-session-observation.md`「延期adoptと反映後の観測」へ進む。

外部操作として扱う対象は、選定結果の`選定`において`レーン`が`なし`、`上流投入`が`なし`以外、あるいは`プロジェクト固有の公開後の操作の順序`が`既定`以外となっている項目である。
また、このセッション中に回答を受け取ったUWI、選定工程で記録されたプロジェクト固有の公開後操作、レーン工程から引き継いだ延期`adopt`の対象もこれに含まれる。
これらが一切存在しない場合は、`agent-toolkit:commit`の`references/push-and-ci.md`「公開状態の4項目」を主作業ツリーから読み取り専用で取得する。
レーンの統合に伴う未公開コミットの有無は、この4項目を通じて検出する。
4項目すべての成立が確認できた場合は、`finish-session-recovery.md`「公開状態の検収と再起動」において検収済みとして扱う。

## 公開担当の起動と並行工程

**2.** 発見済みの独立改善は、発見時に`immediate-fix.md`で処置を決め、必要な証拠が揃った時点から調査・起草・意味確認を進める。

公開結果に依存しない原稿・報告の構成・公開前の実績・結果JSONは先に準備し、未確定のCI・修正後の結果は確定後に補う。
同じSHAの公開CI、必要な修正と再検証、リリースの順序、最終結果の確定、使用中資源の回収条件は維持する。
公開後にも独立して成立するWI投入は、`share/wi-submission.md`の公開受付へ自己完結した要求を渡してよい。
永続受付と独立監督への所有移管を確認した後は、その投入自体を本回のwriter待機へ残さず、受付票と未完了の投入を報告へ区別して記載する。
受付が成立しない場合は既存の投入担当の起動・検収・保存を同じセッションで完遂する。

**3.** レーンの数にかかわらず、各レーン担当から返却された統合結果に基づいて、本書の公開工程へ進む。
計画、実装、レビューに関する定型的な結果報告を再度求めることはせず、最終的なHEAD、計画の保存状況、WIの採否確定、延期対象を検収する。

統合後の独立した即時修正は`immediate-fix.md`「レーンの変更範囲へ加える修正の認可」で担当と時機を決め、統合先の現行HEADで限定検証と独立文脈レビューを実施する。
元WIの未達を除き、統合済みレーンへ戻して再統合を繰り返さない。
公開後なら修正の検収後に公開状態を再検収し、必要な再公開へ接続する。
統合後の即時修正、公開中のCI修正、版数更新後の追随など最後の検証の後に加えた変更は、変更した定義・文言・構成・実行前提の変更前の値を固定文字列で探し、消費する実装・検査・試験を検証対象へ戻してから公開へ進む。
変更前のHEADで得た成功は追加変更の検証に充てず、同じ版・同じ条件の成功だけを再利用する。
CIだけで観測できる条件はローカルの成功で代替せず、公開CIで確かめる。

**4.** 対象リポジトリの規範によって、セッション終了工程と同じタイミングで固有の追跡先への記録が義務付けられている場合は、その記録を完了させてからセッションを終了する。

**5.** 専用worktreeとレーン専用branchはセッション終了まで保持し、公開担当の起動前や処理中に回収しない。
回収はGit共通ディレクトリを変更し、`${CLAUDE_PLUGIN_ROOT}/share/session-termination.parent.md`「起動前の前提」の単一writer条件と競合する。
公開・CI修正を含む全工程と外部プロセスの終了後に回収する。

**6.** 統合後に保存済み計画をcheckoutして履歴書換えへ追随させるメインまたは配下担当は、全計画バンドルを更新・再保存する。
手順は`../../commit/references/history-rewrite-wi-records.md`「WI実装commitの対応の継承」に従う。
成功と警告を検収してから公開担当を起動する。

このとき同一の`plan-rewrite`処理によって、統合時に採否を確定したAWIの対応コミットや件名も、新たなコミットに合わせて更新される。

**7.** メインエージェントはレーン数を問わず、各レーンの記録を対象リポジトリのバージョン採番規範に照らし合わせ、未公開の統合変更に適用すべき最上位の`bump種別`を決定する。

公開担当は既決の種別を使い、バージョン更新、生成物同期、push前チェック、push、CI確認、失敗時の修正、割り当てられた公開後操作、延期`adopt`を完遂する。
直接実行と委譲の選択、入力、検収は`session-termination.parent.md`に、検証とCIの判定は`agent-toolkit:commit/references/publish.md`「検証とCI」に従う。
コミットを外部APIへ渡す処理には同資料「pushと公開状態」の完全OID解決契約を適用する。
主作業ツリーへ書き込む主体は、公開担当と起動したCI修正担当のうち常に1者とする。
公開を委譲したメインは、その間は主作業ツリーとベースブランチを読み取り専用とする。

**8.** 公開はメインまたは対象リポジトリ担当の直接実行を通常とし、`session-termination.parent.md`「直接実行」に従う。

本書の公開担当には、直接実行する担当と委譲された担当の両方が含まれる。
「起動」「返却」「差し戻し」は委譲時の操作とし、直接実行では自身の進捗記録と観測結果から同じ条件で検収し、失敗から回復する。
委譲を選んだときだけ公開担当を起動し、返却を待つ。
実行レビューとCI修正担当の起動条件は変えない。

**9.** 公開担当による公開処理やCIの完了待ちと並行して進めてよい作業は、主作業ツリーとベースブランチへの書き込みを行わず、かつ公開担当の入力を生成しない工程に限られる。
既存の別worktreeでのファイル編集と、そのworktreeのbranchへのcommitはこれに含む。
worktreeの作成・回収のようにGit共通ディレクトリの構成を変える操作は、公開担当の終了後に行う。
並行作業の成果は公開担当の終了後にベースブランチへ統合し、`finish-session-recovery.md`「公開状態の検収と再起動」に従う。

- キューへの投入: `${CLAUDE_PLUGIN_ROOT}/share/add-wi.parent.md`に従い、公開担当の起動と同一の工程で投入担当へ委譲する（書き込み先はprivate-notes）。
  公開担当の実行結果を観測して初めて内容が定まる項目については、結果が返却された後に渡す。
- 読み取り調査とAWI原稿の準備: 公開結果を待たずに調査できる既知の警告の調査や、AWI原稿の起草作業は、主作業ツリーやprivate-notesへ書き込みを行わない独立した担当者へ委ねる。
  準備した証拠や原稿は投入担当へ渡し、返却された原稿をメインエージェントが`../../../share/add-wi-parent-acceptance.md`「原稿の検収」で検収して採否を確定する。
  確定した結果は、その後に送る元の作業完了報告へ含める。
  公開結果が必要となる問題の調査は、結果が確定した後に開始する。
- 独立した追加工程: 長時間を要する公開・デプロイ・CI待機や追加の公開工程については、入力コミット、成果物、配備先、排他制御が必要なリソース、先行工程の成功が前提となるかどうかを、起動前にあらかじめ進捗記録へ記載する。
  独立して進められる追加工程（別の成果物や配備先に対する緊急デプロイなど）は別のエージェントへ委ねて並行稼働させ、先行工程の成功を入力とする工程は、先行側の成功を確かめてから着手する。
  公開担当への指示においては、待機対象とする工程と並行して開始する工程を明確に区別する。
- 常時稼働サーバーへの反映: `SKILL.md`「工程」に定める反映・稼働確認手段が備わるプロジェクトでは、全レーンの統合を検収してリソースを保持したまま、公開担当の起動直後（公開担当の起動を省く場合は統合検収の直後）に反映を開始する。
  反映したHEADの一意な短縮OID、反映コマンドの終了ステータス、および稼働確認の結果を完了報告へ渡す。

主checkoutへの反映と、既存の長命なMCPサーバーのコード更新を区別する。
同じ統括がafter評価・追加レーン・再公開を続け、役割宣言や起動時資料の検査が失敗した場合は、元診断と稼働実装・資源の版を確認する。
版相違が示されたときだけ`wait-anomalies-failures.md`「役割資源との版相違」で新プロセスからの回復へ進み、配下の実行中・結果未回収・停止後再開可能な担当と入力・結果を保護する。
正常な委譲ごとの全量検査や無条件の再接続は追加しない。

**10.** 公開担当がcheckoutした保存済み計画を履歴書換えへ追随させた場合も「WI実装commitの対応の継承」に従い、担当工程の最後に更新した全計画バンドル（延期adopt・未終了を含む）を再保存し、成功と警告を検収する。
メインはその結果を検収してから観測・再開記録用のcheckoutへ進む。

 この場合も同一の`plan-rewrite`処理によって、統合時に採否を確定したAWIの対応コミットや件名が新たなコミットへ適切に付け替えられる。

### 公開担当起動後の会話圧縮

**11.** Claude Codeのメインエージェントは、公開担当を起動するたびに前述の並行工程（投入担当、読み取り調査担当、追加公開工程、常時稼働サーバーへの反映）を先に開始し、長時間の待機に入る前に会話圧縮を予約する。

 予約後の復元では、注入された要約を起点に対象worktree、公開入力コミット、担当別引き継ぎ記録、保存出力と既存の資源登録から必要な情報を取得する。
 予約と復元の手順は`agent-toolkit:delegation`の`references/waiting-claude-code.md`「長時間待機前の会話圧縮」に従い、引数を指定せずに予約し、通知を受け取った後に同一の公開工程へ復帰する。
 公開対象が存在しないため公開担当の起動を省略した場合は、この会話圧縮予約のステップも省略する。

## 本文参照の取得条件

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| AWIの処理の基準を適用する前 | `../SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 公開工程とセッション終了：延期adoptと反映後の観測の基準を適用する前 | `finish-session-observation.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/finish-session-observation.md` |
| 公開工程とセッション終了：公開工程の続行不能の回復と再開の基準を適用する前 | `finish-session-recovery.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/finish-session-recovery.md` |
