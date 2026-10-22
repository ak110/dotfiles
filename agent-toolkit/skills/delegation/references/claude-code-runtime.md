# Claude Codeでの委譲の実装

本書はClaude Codeから委譲するときの実装上の取り決めを定める。共通の送信・受領契約は`agent-toolkit:delegation`のSKILL.md、委譲先選定とworktreeは同スキルの`references/runtime-routing.md`「実行手段」と「実装担当とworktree」に従う。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `runtime-routing.md#「実行手段」「実装担当とworktree」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/runtime-routing.md#「実行手段」「実装担当とworktree」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `runtime-routing.md#「実行手段」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/runtime-routing.md#「実行手段」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `routing.md#「会話を引き継ぐ委譲」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/routing.md#「会話を引き継ぐ委譲」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../SKILL.md#「送信」` | 判断 | `section:agent-toolkit:*/skills/delegation/SKILL.md#「送信」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `waiting-and-monitoring.md#「待機区間の構成」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring.md#「待機区間の構成」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `receiving.md#「受領手段と報告様式の選択」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/receiving.md#「受領手段と報告様式の選択」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `waiting-and-monitoring.md#%E3%80%8C%60atk%20agents%20wait%60%E3%81%AE%E5%BF%9C%E7%AD%94%E3%81%AE%E6%89%B1%E3%81%84%E3%80%8D` | 判断 | `section:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring.md#%E3%80%8C%60atk%20agents%20wait%60%E3%81%AE%E5%BF%9C%E7%AD%94%E3%81%AE%E6%89%B1%E3%81%84%E3%80%8D` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `wait-anomalies.md#「ホストによる背景待機の停止後」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/wait-anomalies.md#「ホストによる背景待機の停止後」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `runtime-routing.md#「継続と新規起動」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/runtime-routing.md#「継続と新規起動」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `waiting-and-monitoring.md#「完了通知を待ってターンを終える場合」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/waiting-and-monitoring.md#「完了通知を待ってターンを終える場合」` |

## 起動パラメーター

`Agent`ツールを使う条件は`references/runtime-routing.md`「実行手段」に従う。モデル、推論の深さ、背景実行と省略時の値は、起動時に公開されたスキーマと定義のfrontmatterで確定する。

`routing.md`「会話を引き継ぐ委譲」がforkを選び、fork modeが有効なときは`Agent`へ`subagent_type: "fork"`を渡す。forkは親のモデルと会話履歴を引き継いで背景で実行され、通常の子と同じ完了通知で結果を返し、親からの1階層で完結する。fork modeは対話セッションでは初期状態で有効、`-p`の非対話実行とAgent SDKでは無効であり、無効な環境では`agents_server`の新規起動を使う。監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/claude-code-runtime.md：起動パラメーター：2026年9月27日」にある。

## 実行時能力と通信scope

toolの公開、呼び出し結果、完了通知の配送はprovider、Claude Code版、設定、通信scopeの組合せで変わるため、観測した組合せにだけ結果を適用する。

| provider・platform | Claude Code版・設定 | 通信scope | 観測結果 |
| --- | --- | --- | --- |
| Claude Platform on AWS | Sonnet 5、環境変数を`0`に設定 | 同一セッション内の親子・子孫 | `SendMessage`は成立したが、`ListAgents`はtool公開されず、独立セッション間通信は成立しなかった |
| Claude Codeローカル | 2.1.241、`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`を設定しない | 同一セッション内の親子・子孫 | `SendMessage`と`ListAgents`の公開および呼び出し成功を確認した |

監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/claude-code-runtime.md：実行時能力と通信scope：2026年9月4日」にある。

- `SendMessage`と`ListAgents`の利用可否は、現在のエージェントへの公開と実呼び出しの成功で判定する。環境変数、provider、agent定義の許可から推定せず、拒否されたtoolは利用不能とする。agent定義の`tools`は許可の上限を示すにとどまる
- `ListAgents`を使えない場合は、保持済みのID、完了通知、成果物観測、自身が起動して識別子を保持したプロセスの生存（停止を伴う場合は停止結果も）を組み合わせて確かめる（以下「代替稼働観測」）。プロセス名の一致から生存や対象を推定しない
- 通常起動のClaude委譲先には、`agents_server`が`agent-toolkit/share/rules-subagent.md`と`agent-toolkit/share/rules-subagent.claude-code.md`をシステム指示へ連結する。`Agent`ツールのサブエージェントには、`SubagentStart`フックが同じ2ファイルを文脈へ追加する。通常起動と軽量起動の違いは`references/runtime-routing.md`「実行手段」が定める
- `agents_server`の委譲先は独立したClaude Agent SDKセッションで、ターン終端で`Stop`が発火する。`Agent`ツールのサブエージェントでは`SubagentStop`が発火し、フック入力に`agent_id`が入る。規範上の役割とフック種別は別物として扱い、最上位限定の工程では`起動経路`の記録で最上位かを判定する

| 通信scope | 宛先候補 | 成立条件 | 成立しない場合 |
| --- | --- | --- | --- |
| 同一セッション内の親子委譲 | `Agent`の起動結果が返すagent ID | 現在のエージェントへ`SendMessage`が公開され、対象IDが同じ委譲ツリーで有効 | `### 完了通知と中継の実行順`に従う |
| Agent Teams | team member名など機能が定める識別子 | 機能が有効で、必要なtoolの公開と呼び出しが成功する | 本文書では設定を変更せず、同一セッション内の親子委譲へ読み替えない |
| 独立セッション間通信 | capability discoveryが返す機械可読識別子 | providerが機能を提供し、`ListAgents`と`SendMessage`の実呼び出しが成功する | 未対応providerでは利用せず、代替機構を追加しない |

宛先には起動結果のagent ID、hostが明示する機械可読reply address、hostが値を提示し到達を確認できた`senderTaskId`だけを使う（`from`、`origin.from`、`name`、`subagent_type`は宛先の解決先から外す）。
稼働中の独立セッションへの即時の依頼は、利用できる宛先列挙と通信手段で到達先を確定して送り、到達できなければ依頼元へ報告する。`atk wi process-loop instruct`は次回起動時に読む指示の保存であり、即時配送の結果とは区別する。

## 委譲プロンプトへ含める前提

- 専用の`<役割名>.subagent.md`かagent定義がある起動の委譲プロンプトは`agent-toolkit:delegation`「送信」に従い、完了報告をツール戻り値で1回返し`SendMessage`で送らない契約はその定義の側に置く。即時通知は`agent-toolkit/share/rules-subagent.md`「確認事項の即時通知」と`agent-toolkit/share/rules-subagent.claude-code.md`「即時通知の宛先」が定める
- 名前付き定義には、前景での完了報告の返し方、`SendMessage`で送らないこと、待機対象の結果を含める要件、人間向けの本文（完了報告、中継本文、エスカレーション、進捗報告、待機表明）を日本語で書くことを置く。書式を固定する機械可読ブロックと固定文字列はそのままとする
- 専用定義を持たない`claude`・`Explore`・forkの委譲プロンプトには、完了報告を日本語で書き、待機対象の結果も含めてツール戻り値で1回だけ返す（`SendMessage`は使わない）指示を含める。forkでは親が持つ背景の再掲を省く
- `rules-subagent.md`と`rules-subagent.claude-code.md`は`SubagentStart`フックが追加するため、読込を指示しない
- 組み込みの`Explore`・`Plan`は親のCLAUDE.mdとメモリー階層、親のgitステータスを受け取らない（同名のユーザー定義かプロジェクト定義で上書きした場合は受け取る。frontmatterで変えられない固定の挙動）。委譲プロンプトへ`01-agent.md`の絶対パスを渡して着手前に全文読ませ、判定に用いる実行系の起動コマンド、検証コマンド、除外範囲と報告様式を明記する
- 委譲先がさらに読み取り専用の調査・レビューを`Agent`ツールで起動する場合は、起動前にmanaged-temp内の成果ファイルの絶対パスを確定して委譲プロンプトへ含める。記載形式は定義ファイルがあればその完了報告全体（委譲プロンプトは参照だけを持つ）、無ければ完了報告形式を委譲プロンプトへ含める。成果ファイルを指定した委譲先には、返信先の解決可否によらず、必須項目を全て含む最終完了報告をそのファイルへ確定してから終端させ、書込みに失敗した場合は成功を返さず通常の完了報告へ失敗を含めさせる。縮約・部分書込み・項目欠落は未完成として扱う

## 検証コマンドの実行形態

待機の起動と回収は`references/waiting-and-monitoring.md`「待機区間の構成」、待機以外の検証・ビルドと遠隔ホストの長時間コマンドは同書「背景ジョブの起動形（Claude Code）」に従う。背景へ移ったコマンドの終了状態を確定してから終端報告を返す規定は`agent-toolkit/share/agents-server-delegate-notice.md`と`agent-toolkit/rules/02-agent-operations.md`「ツール・コマンド運用」が定める。
背景で動くBashコマンド（`run_in_background=true`か、実行環境の判断で背景へ移ったもの）を止める場合は、起動結果か移行通知が返したタスクIDだけを`TaskStop`へ渡す（遅延提示される環境ではスキーマを取得してから呼ぶ）。

## 一時領域と成果物の観測

- `Agent`の`output_file`はセッションのJSONLへのシンボリックリンクになることがあり、リンク自体の`stat`や`ls -l`は変化しなくてもリンク先は追記され得る。補助観測には`atk watch --file <output_file>`か`stat -L <output_file>`を使い、本文の読取は進捗の確認以外の用途に限る。監査記録は`docs/development/audit-records.md`「agent-toolkit/skills/delegation/references/claude-code-runtime.md：一時領域と成果物の観測：2026年10月9日」にある
- 成果物側の状況は`atk watch`を単独で実行して観測する（対象の指定、出力項目、終了コードは`atk watch --help`）。これは補助観測であり、稼働状態は`ListAgents`か代替稼働観測で判定する

## 結果の受け取り方

- 配送方式は戻り値と完了通知（task-notification）の2種である。サブエージェント起動は常に背景実行として扱われるため、直接の子からは完了通知で受け取り、検収は受領した本文で行う。`idle_notification(available)`は待機可能な状態を示すだけで完了の判定に使わない
- 成果ファイルを指定して自身が起動した調査・レビューは`references/receiving.md`「受領手段と報告様式の選択」の例外に従う。稼働中に観測したファイルの内容は途中経過として扱い、終端を観測した後に同じ絶対パスから再読して必須項目を全て含む最終完了報告であることを確かめてから受理する。終端を観測できない場合や、再読でファイルが未作成・読取不能・部分書込み・項目欠落の場合は、絶対パス、終端観測と異常を続行できない理由として返す
- 同じworktreeへ実装担当を引き継ぐ場合は、前任が所有する全プロセスの終了を、完了報告のツール終了結果か実行識別子で確かめてから後任を起動する。前景の担当の終了後にプロセスの存続が判明したら引継ぎを止めて未完了として返し、保持した識別子で終了を確かめた後に限り再判定する
- 背景の実装担当を交代する場合は、全プロセスの終了を確かめた後、残るバックグラウンドタスクや背景エージェントを`TaskStop`で止め、その系統に稼働が無いことを`ListAgents`（使えなければ停止結果を含む代替稼働観測）で確かめてから交代する。未知の子孫が無いことを確定できない場合や、停止後の終端を確かめられない場合は、書込所有権を移さず、保持したIDと観測結果を続行できない理由として返す

### 背景ジョブ完了通知と実プロセスの終了順

バックグラウンドタスクの完了通知が届いた後も、同じ`atk agents wait`の実プロセスが残ることがある。後続の待機の扱いは`references/waiting-and-monitoring.md`「`atk agents wait`の応答の扱い」が定める。
ホストの停止通知も外側のタスクと子プロセスの終了を分けて扱い、`references/wait-anomalies.md`「ホストによる背景待機の停止後」から元の出力の回収と観測の再開を判断する。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/claude-code-runtime.md：背景ジョブ完了通知と実プロセスの終了順：2026年9月21日」にある。

### 完了通知と中継の実行順

`Agent`ツールで新規起動した子の完了通知は委譲元へ届くが、`SendMessage`で継続した相手の完了通知は送信元へ届かず最上位セッションへ届く。このため`SendMessage`で継続した工程は、最上位セッションが中継した場合に再開する。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/claude-code-runtime.md：完了通知と中継の実行順：2026年8月」にある。

1. 委譲元は`Agent`の戻り値から直接の子のagent IDを保持し、宛先にはそのIDだけを使う
2. 委譲先は通常完了報告をツール戻り値で1回だけ返す。即時通知は`agent-toolkit/share/rules-subagent.md`「確認事項の即時通知」の判定で`SendMessage`が選ばれた場合に送る
3. 直接の委譲元ではない祖先が子孫の完了通知を受け取った場合は、`status`と`result`を保持し、`SendMessage`が公開され保持した直接の子のIDへの呼び出しが成功する場合だけ、同じIDへ逐語で中継する
4. `SendMessage`の`success: true`やqueuedは送信側の受理だけを示す。中継先IDと送信後の完了通知か戻り値を対応付けて受信を確かめる
5. `SendMessage`が未公開か拒否された場合、または送信後の結果を観測できない場合は再送を控える。未完了の工程が自身の担当範囲と権限に収まる場合だけ巻き取り、そうでなければ保持した通知内容、送信先ID、送信結果と未観測の結果を続行できない理由として返す
6. 子孫の工程を中止・変更・再開させる指示も直接の子へ同じ手順で送る。直接の子が停止していても同じIDへの継続を先に試み、成立しない場合だけ孫以下へ1回だけ直接送り、発信元のエージェントと役割、指示の理由を本文に明記する

最上位セッションのメインが委譲ツリー全体を取り下げる場合は、`ListAgents`が使えるときに限り委譲元を先に止め、列挙した全子孫IDも止めてその系統に稼働が無いことを確かめる。使えない場合は未知の子孫の不在を確定できないため取下げを始めず、保持したIDと観測結果を続行できない理由として返す。取下げ中に届いた子孫の完了通知は記録にとどめて手順を続ける。

## 稼働状態の観測と継続依頼

- `ListAgents`と`TaskStop`は自身のツール一覧に公開され呼び出しが成功する場合だけ使う
- 背景の委譲の稼働状態は`ListAgents`で観測する。進捗だけを確かめる回は、成果物パスが確定していれば`atk watch`で判定して`ListAgents`を呼ばず、停滞の判定、催促か巻き取りの要否と宛先の確定、終端の観測で呼ぶ。成果物パスも保持したプロセスも無ければ、進捗の報告を省いて完了通知を待つ。完了直後の委譲先は列挙されないことがあるため、列挙されないことは終了を支持する材料にとどめる
- `ListAgents`か`TaskStop`が使えない場合は、起動結果の識別子を保持し、出力ファイルの末尾標識（回数上限付き）、`atk watch`、保持したプロセスの生存照会を組み合わせて判定する。更新が続く間は完了と決めつけず催促にとどめる
- 待機の解除手段と、MCPツール呼び出しが背景へ移った場合の扱いは`references/waiting-and-monitoring.md`「待機区間の構成」、`Agent`ツールの担当の継続条件と配送不能の判定は`references/runtime-routing.md`「継続と新規起動」に従う

## 待機中の定期再確認と背景転換

未完了の委譲か背景処理を待つメインは、完了通知を待機の主な解除手段としたまま、`CronCreate`、`CronList`、`CronDelete`が公開される場合だけ定期再確認を併用する。`ScheduleWakeup`は`/loop`専用とする。
定期再確認を装着するのはメインだけとする。`Agent`ツールのサブエージェントは装着しない（`CronList`はメインと共有され、サブエージェントのために作成したtaskのpromptは親の会話へ届くだけになる）。`agents_server`の委譲先は独立したセッションのメインとして扱う。

`agents_server`の`start`では、agent-toolkitのmodが自身の`CronList`・`CronCreate`・`CronDelete`の許可を確認し、メインの最初の`start`の処理の中でtaskを作成する。task IDか装着できなかった事実と理由をその`start`の結果の後に会話へ届ける。既にtaskを持つメインとサブエージェントの`start`では作成と通知を省く。mod自身が削除できない場合は作成を見送り、完了通知による待機を保つ。装着失敗の通知を受けたメインは、通知の理由と次の操作に従う。メインが自ら装着するのは、必要なCronツールをすべて使える次の場合である。

- `start`の結果の後に装着の通知が無く、`CronList`に後述の標識を1行目に持つtaskも無い場合（modが読み込まれない環境など）
- `Agent`ツールの委譲先かバックグラウンドタスクを、そのセッションで最初に起動する直前

自ら装着する場合は`atk wait-schedule --request-bucket main --format json`を1回実行し、出力の`cron`と`prompt`を変更せず`CronCreate`へ渡して`recurring: true`で1件だけ作成する（`prompt`はmodと同じ共通本文）。promptの1行目は`<atk-auto source="periodic-recheck" kind="periodic-recheck">`だけの行である。UserPromptSubmitフックはこの行で機械注入ターンを判定し、resume後の所有taskの確認もこの標識の完全一致で行う。
監査記録は`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/claude-code-runtime.md：待機中の定期再確認と背景転換：2026年10月4日」にある。

- 定期promptが持つのは、待機対象を記録側から列挙する手段、対象ごとの成果物を決める方法、動的に解決したパスを渡すコマンド形（`atk watch --worktree "$worktree_path"`か`atk watch --file "$artifact_path"`）だけとする。待機対象ID、成果物の絶対パス、コミット識別子、残工程と完了済み工程は発火の各回に記録側から読み直させる（可変状態の写しは工程が進むと事実と一致しなくなる）
- 定期promptは、blocking waitとそのバックグラウンドタスクに所有されていない待機対象について記録側の状態と完了通知を再確認し、完了対象があれば受領手順へ進ませる。所有されている対象への照会は省く（所有の判定は`references/waiting-and-monitoring.md`「完了通知を待ってターンを終える場合」）。全対象が未完了なら、閾値に達した経過時間起動の義務だけを実行し、無ければ報告を出力せず待機を続ける。発火自体は完了と停滞の判定の入力から外す
- セッション固有の経過時間起動の義務（定期報告、cooldown解除、期限監視など）は共通本文の外にある。義務がある場合は、義務ごとに経過を実際に測るコマンドと判定閾値を1行ずつ共通本文の終了タグ（`</atk-auto>`）の前へ加え、保持したtask IDを`CronDelete`してから同じ標識のpromptで`CronCreate`し直し、新しいIDを以後の対象にする。義務が後から確定した場合はその時点で作成し直す
- cron式は壁時計基準で、REPLがidleのときだけ発火し、再帰taskは7日で失効し、発火時刻にjitterが加わり得る（`CronCreate`のtool定義で確認する）
- task IDはprompt外で保持し、未完了の対象がある間は同じtaskを再利用する（modの通知で受け取ったIDも同じ）。resumeかcompactionの後は保持したIDを`CronList`の実在taskと比べ、IDを失っていれば標識の完全一致で所有taskを一意に特定できる場合だけ再利用か削除へ進む。一意に特定できないtaskへの操作と重複作成は控える
- modは全待機対象の終端と結果回収後に、共通promptと一致する非durableの所有taskを自動回収する。複数対象の一部だけの終端、背景処理、未配送の完了通知、agents_serverの未回収結果が残る間は保持する。モデルへの`CronDelete`の公開は自動回収の条件に含めない
- 経過時間起動の義務を追加したtaskは通常回収で保持し、既存のセッション終了時の自動回収へ渡す。無関係なcronとdurableなtaskは通常回収の対象外である。回収判定が得られない場合と自動回収の拒否・失敗時もtaskを保持する。失敗通知を受けたメインは理由と次の操作を確認し、modは次の有効な終了判定で再評価する
- modが無い環境で自ら装着したtaskは、全対象の終端を確かめた時点で`CronDelete`し、削除を確かめる
- `CronCreate`、`CronList`、`CronDelete`が使えないか`atk wait-schedule`が失敗した場合は、`sleep`や背景タイマーへ切り替えず、完了通知を待つまま待機表明でターンを終える（`sleep`で外部事象だけを待つと、事象が届かない障害で打ち切る契機を失う）

`run_in_background`を指定しないBashコマンドが実行環境の判断で背景へ転換された場合も定期再確認の対象とする。転換時の起動結果が返す出力ファイルの絶対パスに対し、完了時に同じパスを含む完了通知が届く。

- コマンドの結果はその出力ファイルを`Read`で読む。サブエージェントの出力ファイルはセッション記録の全文であり、読むとコンテキストを圧迫するため、結果は起動形態の終端応答から得る
- 通知で同じセッションを再開できる待機は「待機区間の構成」の背景起動と通知後の回収へ統一する。通知を持たず同一ターン内の回収が必要な場合は、保持したパスの末尾の終端標識を回数上限付きで照会し、標識が得られなければListAgentsか代替稼働観測で確かめる。稼働状態を確定できなければ書込所有権を移さず続行できない理由として返す
- `sleep`の直接実行、条件が初回から成立するループ、専用の待機機構の新設で待機を代替しない

## 計画ファイル作成のplan mode下委譲

サンドボックスパスへの書き込みをメインが検収して正規パス（`~/.claude/plans/`配下）へ反映する。正規パスの削除・移動はメインだけが行う。
