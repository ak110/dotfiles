# branch・CI所有権・公開・終端の設計記録

本書は[設計記録の索引](design.md)から主題別に分割した記録であり、機構の目的、構造の理由、知識境界と却下した代替案を保持する。
実行時に適用する規範は、各節が参照する現行のルールファイルとスキルが定める。

## developとmasterのbranch・リリース設計

`develop`を開発用branch、`master`をリリース用branchおよびGitHubのdefault branchとする。
`master`への更新はPRのマージコミットだけに限定し、直接pushを許可しない。
PRの作成は、条件が成立する`agent-toolkit:process-wi`の実行ではエージェントが実施し、それ以外では手動で行う。
実施可否と条件の判定は`dotfiles-release`スキルが定める。
head branchの機械的な限定は設けない。

repository設定ではマージコミットを有効にし、squash merge、rebase mergeおよびauto-mergeを無効にする。
マージ後のbranch自動削除も無効にし、エージェントが同期状態を検収した後にだけ次の工程へ進める。
GitHubのdefault branchは`master`のまま維持する。

`master-release-pr`という固定名のactiveなbranch rulesetを1件だけ使用する。
rulesetのbypass主体は空にし、PR経由の更新、会話threadの解決、最新の`master`を含む次の7必須check、削除禁止およびforce push禁止を設定する。

- `test-linux`
- `test-windows`
- `python-lint (3.13)`
- `python-lint (3.14)`
- `browser-e2e`
- `rust-lint`
- `statusline-version`

### CIの実処理所有権

共通CIは`push`と`master`向け`pull_request`の全イベントでjobを開始し、job表示名とmatrixを評価する。
共通jobにはjob-level `if`を置かず、step-level条件で非所有markerと既存実処理を切り替える。

`test-linux`と`update-dotfiles-upgrade (windows)`は全履歴を取得し、現行HEADの時刻から約72時間前の祖先を選ぶ。
管理一時ディレクトリ内へローカルbare remote、旧版checkoutおよび隔離HOMEを構成し、旧版をchezmoiで適用する。
その後、同じbranchのremote refだけを現行HEADへ進め、旧版checkout内の`bin/update-dotfiles`または
`bin/update-dotfiles.cmd`を起動する。公開ランチャーの終了コードが0で、更新後checkoutの`git rev-parse --short=7 HEAD`が
検証開始時の現行HEADと一致した場合だけ成功とする。実行中のOSアカウントのHOMEと外部remoteは変更対象にしない。

| イベント | head repository | head branch | base branch | 共通8 jobの実処理所有者 | 表示名 |
| --- | --- | --- | --- | --- | --- |
| `push` | base repository | 任意 | 該当なし | `push` run | 8件のrequired check名 |
| `pull_request` | base repository | `develop` | `master` | `develop`の`push` run | 8件の`(non-owner)`名 |
| `pull_request` | base repository | `develop`以外 | `master` | `pull_request` run | 8件のrequired check名 |
| `pull_request` | base repository以外 | 任意 | `master` | `pull_request` run | 8件のrequired check名 |

同一repositoryのheadが`develop`、baseが`master`のpull requestでは、共通jobの先頭で非所有markerだけを成功させ、checkoutを含む既存実処理を実行しない。
このrelease pull request以外の同一repository pull requestとfork pull requestでは、非所有markerをskipして既存実処理を実行する。
非所有markerはcheckout前から存在する`${{ github.workspace }}`を作業場所とし、`test-windows`は`pwsh`、その他の共通jobは`bash`を明示する。
`rust-lint`は既存jobの`defaults.run.working-directory`を維持し、非所有markerだけがworkspace rootを明示してその指定を上書きする。

job-level条件を使うと条件が偽のjobがmatrix展開前にskipされ、job名式が評価されないため、非所有時の8件の表示名を保証できない。
共通jobを開始して非所有markerを成功させる構成により、required check名と異なる表示名を生成し、同名のskip-successで所有runを代替しない。
pull requestの`GITHUB_SHA`はrunnerがcheckoutするtest merge commitを示すが、check runの`head_sha`はstatusを関連付けるpull request head commitを示すため、両者を同一視しない。
`master`が`develop`の祖先であり、release merge commitのtreeが`develop` headのtreeと同一になるrelease invariantを、共通CIの実処理を`develop`の`push` runへ帰属させる根拠とする。

実ブラウザーE2Eは、共通`python-lint`から分離した`browser-e2e` jobが所有する。
同jobはPython 3.14でPlaywright Chromiumを導入し、`agent-toolkit/agent_toolkit/_atk/serve/browser_test.py`だけを実行する。
分離により、律速となる`python-lint (3.14)`からChromiumの導入とE2Eの実行時間を外したうえで、最新のPythonでのE2E実行を維持する。
`browser-e2e`は他の共通jobと同じ非所有markerの構成を採用し、required checkへ加えることでE2Eの失敗がマージを遮断する状態を保つ。

Windowsの旧版更新検証は`update-dotfiles-upgrade (windows)`が所有し、`test-windows`はWindows固有pytest、公開ランチャー、通常profileへのchezmoi適用とpost-applyの確認を所有する。両job間に依存関係を置かず、更新検証の通常profile監視に必要なMozilla予約タスク停止は更新検証jobが実行する。

`python-lint`のmatrixは`python-lint (3.13)`、`python-lint (3.14)`、`pytest (3.14)`の3要素である。Python 3.14のpytest以外の確認とpytestを別runnerで並行し、cache keyにPython版と実行種別の両方を含める。

statuslineのCargo versionとbase・head versionおよびtagの確認は、共通`rust-lint`から分離した`statusline-version` jobが所有する。
`statusline-version`は`pull_request`かつbaseが`master`の全pull requestと、`develop`へのpushで実行し、head repository、head branchおよびrelease条件を追加の限定に使わない。
比較基点はpull request起点では`github.event.pull_request.base.sha`、push起点では`git fetch --no-tags origin master`の後の`git merge-base`が返す`master`との共通祖先とする。
push起点を加えるのは、版数更新の抜けをrelease pull requestの必須check一式が実行される前に検出するためである。
判定は`scripts/check_statusline_version.py`へ集約し、CIの同jobとpyfltrのcustom-command`statusline-version`の双方から呼ぶ。レーン担当は版数更新の根拠を渡し、終端担当が版数を更新する。pyfltr側は`pyproject.toml`で無効にしておき、終端担当が公開前のローカル検証で明示的に有効化する。比較先は作業ツリーとしてcommit前の変更も判定し、push前に版数の更新忘れを見つける。pre-commitの`pyfltr fast`で版数更新前の途中commitを遮断しないよう非fastとし、浅いcheckoutの`python-lint` jobでは無効化する。
同一repositoryのreleaseおよびnon-release pull requestとfork pull requestが同じ検証対象となり、`rust-lint`というrequired名の重複を生成しない。
ruleset `21524717`のrequired checkは共通8名と`statusline-version`の9件とし、`statusline-version`以外は共通CIのjob表示名と一致させる。
ruleset更新前の個別GETでは、応答の完全IDが`21524717`、`source`が`ak110/dotfiles`、`target`が`branch`であり、条件が`refs/heads/master`を対象とすることを確認する。確認した完全IDは、送信前後の個別GETとPUTのURLパス`repos/ak110/dotfiles/rulesets/21524717`へ固定する。
ruleset更新の本文はmanaged-tempの中のJSONファイルへ保存し、送信前に保存したJSONファイルを読み戻す。トップレベルキーが`name`、`target`、`enforcement`、`bypass_actors`、`conditions`、`rules`のいずれかであり、`id`を含まないこと、`refs/heads/master`条件、9件のrequired check名を検証する。
検証に成功した同じファイルを`gh api --method PUT --input <検証したJSONファイルの絶対パス> repos/ak110/dotfiles/rulesets/21524717`へ渡す。擬似端末の標準入力を更新本文の搬送に使わない。
更新要求が失敗した場合は、追加のPUTを実行する前に対象rulesetを個別GETで再取得する。再取得した現行状態が更新前状態と完全に一致し、送信するJSONファイルの内容が検証時から変化しておらず、失敗の原因が本文の搬送であって送信方法をファイル入力へ是正できることを確認できる場合だけ、同じ本文の再送を1回だけ許可する。
現行状態を取得できない場合、現行状態が更新前状態と一致しない場合、更新後の確認が期待値と一致しない場合は再送せず、更新前状態と現行状態を保持して`needs_escalation`で終端する。
更新本文を擬似端末の標準入力へ渡す案は、本文が欠落した場合に更新未成立と適用結果不確定を区別できないため採用しない。全ての更新失敗を状態不明として一律に再送禁止とする案も、更新が成立していないことを再取得で確定できる場合まで工程を停止させるため採用しない。

ruleset一覧には個別ref条件が含まれないため、同名候補の完全なIDを取得した後に個別GETで対象repository、branch rulesetおよび`refs/heads/master`を確認する。
候補が0件の場合は作成し、1件の場合は完全IDへPUTする。
複数件、取得失敗または対象不一致の場合は設定を変更しない。
`required_linear_history`はマージコミット要件と両立しないため設定しない。

GitHubのruleset API仕様は、2026年8月26日時点の[Rulesets REST API](https://docs.github.com/en/rest/repos/rules?apiVersion=2026-03-10)を参照する。
workflowの`workflow_run`入力境界は同日時点の[workflow_runイベント仕様](https://docs.github.com/actions/using-workflows/events-that-trigger-workflows#workflow_run)を参照する。

PRマージ後は、`origin/master`をマージコミットの基準として保持し、`git rev-parse --short=7 origin/master`で人間可読の識別子を取得する。
その後に`origin/master:refs/heads/develop`を明示したrefspecで`origin/develop`をpushする。ローカルbranchを`origin/develop`更新の操作元にしない。
マージ後のmaster pushと同期後のdevelop pushに対するCIは待機しない。`master`へは`develop`からのリリースPRだけをマージし、マージ後は`develop`を`master`へ同期するため、マージコミットのツリーはマージ前に必須checkの成功を確認したPR headのツリーと同じになり、developへ載るのも同じマージコミットである。両pushのCIは同じ中身の再実行であり、待機してもリリースの完了時刻が後ろへずれる以外の効果が無い。以前は「commitが同一」「ツリーが同一」などの省略条件を個別に判定していたが、マージコミットをdevelopへ同期した直後にdevelop側の条件が成立せず、同じ中身のCIを待つ事象が起きたため撤去した。必要なRelease statuslineのrun・タグ・GitHub Release・2成果物、origin/developとorigin/masterの最終commitが一致することの確認は省略しない。Release runはmaster CIの成功を契機に起動するため、statuslineの変更を含む場合のmaster CIの結論はRelease runの検収で確かめる。
同期push後に`git fetch origin develop master`する。`git rev-parse --short=7 origin/develop`と`git rev-parse --short=7 origin/master`を個別に実行し、各出力の一意な短縮OIDを比較して、マージコミットとdevelopへ同期したコミットの一致を確認する。ローカル`develop`を同期した場合は、`git rev-parse --short=7 develop`も同じ一意な短縮OIDであることを確認する。

`origin/master`の第一親との差分にstatuslineが含まれる場合は、同じcommitの`Release statusLine` run、タグ、GitHub ReleaseおよびLinux・Windows assetを検収する。`gh run list --commit`が完全なSHAを要求するため、この呼び出しの直前に限って`origin/master`を完全OIDへ解決し、永続化しない。
statuslineの差分がない場合はRelease成果物を検収しない。
成功時は`origin/develop`と`origin/master`がマージコミットと同じcommitであることを7文字以上の一意な短縮OIDで確認する。ローカル`develop`の同期は同期を実行する直前に作業ツリーのclean、現在branchが`develop`であること、マージコミットへのfast-forward可能性を再取得し、すべて成立した場合だけ実施して、リリースの成立条件から分離する。現在branchを更新するコマンドを事前判定の結果だけで実行しない。同期を実施した場合はローカル`develop`の参照を更新し、条件が成立せず同期を省略した場合は本手順がローカルの作業ツリーとローカルbranchへ書き込まない。いずれの場合も、待機中に生じた変更を含めてローカルの状態をリリースの成否判定に用いない。
ローカルの同期状態をリリースの成立条件へ含める案は、公開対象がrefで固定された後も無関係な作業中の差分でリリースを停止させるため採用しない。

初回branch初期化は1回だけ実行する。
実装済みHEADのrefからローカル`develop`を作成して公開し、developのCIを確認する。
`origin/master`の移行前OIDを保存し、初回リリースPRを作成しない。
ローカル`develop`と`origin/develop`のOID一致を確認した後、ローカル`master`の削除直前OIDを記録して削除する。
公開またはCIが失敗した場合はローカル`master`を削除せず、成立済みの外部状態と再開点を報告する。

PRマージ、branch同期およびRelease検収の詳細は、プロジェクトスキル[merge-pr](../../.claude/skills/merge-pr/SKILL.md)を実行時の手順とする。

## 終端工程

起動形態に依存しない版数更新、全体検証、push、CI確認、公開状態とプロジェクト固有の公開後の操作の手順は`agent-toolkit:commit`の`references/publish.md`を基準とする。終端担当と単一レーンはその文書を読み、CI修正の委譲など固有の受渡しだけを保持する。協調モードは着手前に確認した公開範囲まで同じ手順を適用する。公開のたびに起動形態別の手順を複製する案は、版数とCIの判定が分岐するため採用しない。

commit以降のリリース、PR/MRまたは公開操作は、実装のレーンと分離する。
自動コードレビュー監査の処置は、選定工程の開始時からレーン工程と並行して進め、公開工程の開始より前に完了する。受領したpickerの有効出力の検収とレーン起動は、監査の終端を待たずに進める。全レーンのffマージとレーンごとの`adopt`・資源回収後に、メインは1件の終端担当へ委譲し、版数・生成物同期、全体検証、push、CIおよび終端工程を未公開の差分ごとに実行させる。

メインは長時間の公開待機と追加工程の入力commit、成果物、配備先、排他資源および先行工程の成功結果を起動前に比較し、依存関係を記録する。独立工程は別の実行主体へ渡して待機中に進め、同じDB migrationの成功を要する工程はその結果を確認してから始める。終端担当は主作業ツリーへの唯一の書込主体であり、別主体へ渡す工程の書込先は分ける。全工程を終端担当の返却後へ直列化する案では、独立した公開操作も長時間待機の終了まで開始できないため採用しない。
公開結果に依存しない既知の警告は、終端担当のCI待機と並行して読み取り調査とAWI（未完了の作業要求）原稿を準備する。正式な候補選別と原稿の採否は公開後の同じメインが確定し、WI投入担当への委譲で投入する。先行調査の結果を現行状態と比べずに投入する案は、公開結果で消えた問題までprocess-wiの次の実行へ渡すため採用しない。

開発機の常時稼働サーバーへの反映は、全レーンの統合と専用資源の回収後、対象リポジトリへ書き込まず公開工程の入力や排他資源と競合しない場合に、終端担当の公開・CI待機と並行して始める。開始をCIの終端後まで延ばすと、反映の開始が公開の待機時間だけ遅れる。開始時機と反映したHEAD・稼働確認結果の保持は`agent-toolkit:process-wi`の`references/finish-session.md`が担い、単一レーンの同等の時機は`agent-toolkit:single-lane-process`の実行順が担う。`agent-toolkit:completion-report`は先行結果と報告時点のHEADを比べ、同じHEADへの成功済み反映を再実行せず、追加commitや先行失敗があれば現行HEADを反映して稼働を確認する。反映を完了報告だけへ置く案は開始が公開待機後となり、反映を完了報告から完全に外す案は追加commitと先行失敗を反映できないため採らない。

レーンは計画の`## 検証`の`変更範囲の検証`行が挙げる検証だけを実行し、対象リポジトリ全体の検証はレーンで実行しない。
終端担当は対象リポジトリの全体検証をpushの後のCIの結論で判定し、pushの前にはCIが実行しないチェックと、統合後にだけ結果が確定する全体走査のチェックだけを実行する。
CIが実行しないチェックの集合は、プロジェクト規範の定めか、CI定義の起動形・有効なチェックの集合・各チェックが読む設定・実行環境の比較で求める。集合を確定できない場合だけ、全体検証をpushの前に1回実行する。
CIと同じチェックをpushの前に重ねると、重複する検証の所要時間だけCIの開始が遅くなる。一方、CIが実行しないチェックまでCIへ委ねると、その種類の失敗が公開後まで残る。
レーンの変更範囲の検証は、変更を読む側のテストを機構の類型で集める一般則（`agent-toolkit:check-execution`の`references/verification-scope.md`）で選ぶ。確定的な回帰をpush前に止める役割は全体検証ではなくこの対象選定が担い、プロジェクト側は類型ごとの値（マーカー名、コマンド、パス）だけを持つ。対象選定をプロジェクト固有の規定へ事象ごとに追加する運用では、表面構造が異なる次の事象を毎回被覆できなかった。
pushの前に実行したチェックが失敗した場合はCI修正と同じ手順へ進み、pushとCIの往復を経ずに修正する。

`agent-toolkit:process-wi`は選定工程の開始時に自動コードレビューを1回確認し、確認時点の未対応指摘を是正するか、AWIへ記録する。review本文は状態を問わず全てのPR・MRを対象に取得する。inline commentとthreadは未解決threadを持つ対象だけを取得する。監査自体が是正済みと対応不要のthreadを解決するため、未解決threadの有無で限定しても未処置の指摘を取りこぼさない。取得はいずれもpaginationの終端まで行う。review本文の取得は横断クエリーの数回で終わるのに対し、inline commentの取得はPR数に比例した照会を要するため、限定の対象を後者に置く。新しいレビューの到着を能動的に待機しない。直前のセッションが公開したPull Requestのレビューは次のセッションの選定工程で対象へ入る。

状態を問わず全件を毎回取得する案は、Pull Request数に比例するinline commentの照会を削減できないため採用しない。

終端担当の完了後に`agent-toolkit:completion-report`を起動する。同スキルは元作業の完了報告を先に出力し、現在のセッションを対象とする`agent-toolkit:session-review`を起動する。振り返りが対策のAWIを投入する場合は投入の前に振り返り結果を予告し、担当の終端後の応答で振り返りの最終報告（投入が1件以上なら`## AWI投入結果報告`、0件なら`## 振り返り結果報告`）を出力し、続けて`atk agents-exit-session`を単独で実行する。各工程は同じ完了本文を再生成しない。

終端担当の返却後と振り返り後に、メインはベースbranch、追跡ref、最新HEADのCIと作業ツリーを確認する。前回push後に是正レーンまたは主作業ツリーのcommitが統合された場合は、全レーンの終端と書込主体の解放を確認してから追加差分だけを終端担当へ再び渡す。前回公開済み成果の版数更新、プロジェクト固有の公開後の操作、延期adoptを重ねず、最新HEADのpushとCI成功を同じセッションで検収する。新しいcommitが無ければ再起動しない。起動回数で重複公開を防ぐ案は追加成果を未pushのまま残すため採用しない。

終端担当はpushの完了後にベースbranchの公開状態を観測し、`ベースbranchの状態`として返す。メインは`agent-toolkit:completion-report`の完了報告の前に同じ項目を1回観測する。終端担当が返却した時点のOID一致だけを判定に用いると、作業ツリーの未コミット差分と中断状態が観測されず、以降の工程を経た後の状態も対象に入らないためである。終端担当後の是正commit以外の理由で解消できない場合はUWI（ユーザーの判断を要する確認事項）へ引き継ぎ、観測を経ないまま完了報告へ到達させない。

公開状態の4項目は、Gitのporcelain v2のbranch情報と通常の`status`表示から取得する。前者でbranch、未コミット差分および追跡先からのaheadを判定し、後者でrebase・merge・cherry-pickの進行を判定する。親と終端担当は同じ取得形を使う。`.git`内部ファイルの探索を組み合わせる案は、linked worktreeのGitディレクトリ（`--git-dir`）の配置を呼び出し側へ漏らすため採用しない。

終端担当はCIが検証した入力commitを`CIで検証したcommit`、プロジェクト固有の公開後の操作が公開した最終commitを`ベースbranchのHEAD`として分けて返す。プロジェクト固有の公開後の操作がcommitを生成する場合は両OIDが一致しないため、メインはCIの成功を`CIで検証したcommit`だけに適用し、ベースbranchと追跡refの一致を`ベースbranchのHEAD`で検収する。

本文の明示記載を不可逆操作の認可とし、明記のない操作はUWIへ送る。
診断目的でCIを再実行した場合も同一baselineを監視し、許容回数の上限後に失敗が残れば、
CI未通過と帰属判定を終端記録へ残す。

GitHubのpush後CIは、baselineにない同一SHAのrunを監視対象とする。ただし`event`が`dynamic`かつworkflow名が
`Dependabot Updates`であるrunは、対象pushへ帰属しない自動更新として除外する。同名workflowの手動実行と、
他workflowの`dynamic`実行は除外しない。SHA一致だけで除外すると手動診断の失敗を見逃し、workflow名だけで
除外すると起動契機の異なるrunを同一視するため、両fieldの積を境界とする。

メインは全レーンの統合結果、公開先、ユーザーの認可およびCI結果を知る。
レーンは担当commitと変更範囲の検証を知るが、他のレーンの完了状況や公開操作の認可を知らない。
固有指示で`adopt`を延期した項目は、指定されたベース反映後に完成条件を観測した担当が終端する。反映後の新プロセスでしか観測できない条件が残る項目は、観測を終えるまで`processing`を保つ。

この順序により、実装は公開操作と分離して進められ、公開対象を統合後の1つの変更集合へ固定できる。
レーンへ公開を委譲する案は、公開の重複と認可の分散を生むため採用しない。
明記のない不可逆操作を技術判断で補う案も、ユーザーの認可範囲を拡張するため採用しない。
統括の詳細な手順は`agent-toolkit/skills/process-wi/references/run-lanes.md`に従う。

公開対象が1つも無いprocess-wiの実行では、終端担当を起動せずに公開工程を短絡する。pickerの項目は、レーン外操作、`上流投入`またはプロジェクト固有の公開後の操作の順序を持つものだけを数える。レーンの統合による未公開のcommitは、記録した処理開始時のHEADと統合結果を比べず、公開状態の4項目のうち追跡refとのahead値（`# branch.ab`が`+0`でないこと）で検出する。回答済みUWI、プロジェクト固有の公開後の操作および延期`adopt`も無い場合、終端担当が実行する版数更新、生成物同期、push、CI確認およびプロジェクト固有の公開後の操作は対象を持たない。短絡の可否は、終端担当がpush後に観測するのと同じ4項目をメインが取得して判定する。選定された通常レーンの件数を操作量とみなす案は、実装commitを持たないprocess-wiの実行まで公開工程へ送るため採用しない。

手動で起動した`agent-toolkit:process-wi`では、pickerが固定したAWIの完成条件に操作、対象および外部可視の結果が明示された終端工程を、起動したセッションの承認範囲として選定工程で保持する。レーン担当と終端担当が同じ範囲を要求する場合は再承認を求めず、範囲が広がる差分だけをユーザー確認する。手動起動の指示はpickerが固定したAWI集合の完遂を委任する指示であり、同じ範囲の承認を工程ごとに繰り返す運用はユーザーの操作を増やすだけで認可の範囲を変えないためである。自動常駐起動を同じ扱いにする案は、起動時点でユーザーが対象集合を確認していないため採用しない。
