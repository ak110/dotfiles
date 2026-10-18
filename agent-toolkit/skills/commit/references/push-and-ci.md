# pushとCI通過確認

pushするエージェントがpush先、更新ref、基準情報、CI監視、証拠用一時領域を所有する。通常commit、stage、messageは親スキル、CI失敗の帰属と原因分析は`agent-toolkit:bugfix`に従う。

## 公開状態の4項目

ベースbranchの公開を確かめる次の4項目を「公開状態の4項目」と呼ぶ。判定の時点で現在のGit状態から取得し、全て成立した場合だけ公開済みとする。取得には各項目のコマンドを使い、`.git`内部のパス探索と、複数のrefを1回へ渡す`git rev-parse --short`を避ける。

1. `git -C <対象リポジトリの絶対パス> status --porcelain=v2 --branch`の`# branch.head`がベースbranch名と一致する
2. 同じ出力で`#`で始まらない行が0件である
3. 同じ出力の`# branch.ab`のahead値が`+0`である（追跡refが無く取得できなければ不成立）
4. `git -C <対象リポジトリの絶対パス> status`にrebase・merge・cherry-pickの進行中の表示が無い

## 公開先に対応するCI定義の判定

承認済みpush先のforge、repository（ホストを含む）と対象commitを入力とし、その公開先で実行されるCI定義を判定する。
判定を記録する側と受理する側は本節を共通に使い、候補を公開先のforgeに属する定義へ限定する。
複数の公開先や更新refでは組ごとに判定し、対象commitのtreeを証拠に使う。

| 公開先 | 判定する定義と不在の根拠 |
| --- | --- |
| GitHub | 対象commitの`.github/workflows`直下にある、拡張子が`.yml`か`.yaml`のblobを候補とする。`git ls-tree -r <対象commit> -- .github/workflows`のtypeとパスを判定し、終了コード0で候補0件なら不在とする。README、入れ子のYAML、treeは数えない |
| GitLab | 対象projectの有効設定が選ぶCI設定とAuto DevOpsを判定する。標準の`.gitlab-ci.yml`、独自の`ci_config_path`が指す同一project・別project・外部URLの設定、継承を含むAuto DevOpsの有効状態を確認する。対象commitと設定の参照先を対応付け、有効な定義が無くAuto DevOpsも無効と確定した場合だけ不在とする |

GitLabの設定は[Projects API](https://docs.gitlab.com/api/projects/)と[独自CI設定の仕様](https://docs.gitlab.com/ci/pipelines/settings/#specify-a-custom-cicd-configuration-file)を使う。
設定応答の権限による項目欠落と、継承する[Auto DevOps](https://docs.gitlab.com/topics/autodevops/)の有効状態を確かめる。
GitLab CIの不在には、標準ファイルの不在と有効設定の確認を合わせた証拠を要する。
独自設定が指定されているのに参照先を取得できない場合は未判定とする。

取得失敗、権限不足、切り詰め、設定項目の不足で集合を確定できない場合は未判定とし、CI成功や不在へ読み替えない。
定義があってトリガーが発火しない場合はrun未登録であり、定義不在と分ける。
判定の記録には公開先、対象commit、取得したtree・有効設定と参照先、実行コマンド、終了コードと診断を残す。
不在を確定した場合はbaselineと監視を省き、ローカル全体検証の成功と組み合わせて終端状態を「CI定義なし」とする。
未判定は「公開工程の続行不能の受渡し」へ渡し、定義の判定か同じ対象commitのCI照会を回復する。

## push前

- pushの許可（計画に記録した元の要求か確認回答、委譲プロンプト、ユーザー指示）が対象リポジトリと対象branchを含むことを確かめる。amendやfixupの直後は未コミット差分が残っていないことも確かめる
- `git fetch`後に上流との差分を双方向で確かめ、上流が進んでいれば追随して検証をやり直す
- push方式はdry-runで確定する。`git remote -v`、現在branch、追跡branch、`push.default`と承認済みdestinationから、引数なしpushの到達先を判定する。
  承認済みdestinationへ届くと確定すれば引数なしの`git push --dry-run --porcelain`を使う。届かないか判定できなければ`<remote> <source>:refs/heads/<destination>`を明示したdry-runを使う（引数なしpushの失敗が設定から確定する構成では引数なしのdry-runを省く）。
  成功したdry-runの全status lineが承認済みremote・destinationへのrefspecを示す場合だけ、その方式でpushする。拒否・失敗予定のrefや承認外のremote・destinationがあればpushを中止する

CI定義の有無は「公開先に対応するCI定義の判定」で確定する。
委譲元がCI通過をこのセッションで判定しないと明示した場合も、baselineと監視を省く。

CIを判定する場合は、push前に次のとおりbaselineを作成する。

1. セッションのmanaged-temp（`agent-toolkit:managed-temp`）にpushごとの別ディレクトリを作成する
2. 削除refを除く更新refごとに、確定したrefspecの左辺`<source>`をそのまま`--source-ref`へ渡す（右辺、destination ref、remote-tracking refは別の値）。補助スクリプトはsource refをcommitまで再帰的にpeelし（annotated・lightweight tagとも）、完全長commit SHAを保存する。commitへpeelできないrefではbaseline作成が失敗する
3. 読み込んだ本文書の絶対パスからplugin rootを確定する。`(destination ref, source ref)`ごとに次を実行する（`wait_ci.py`の所在は`<plugin-root>/agent_toolkit/`であり、`<plugin-root>/skills/commit/scripts/`とは別）

   ```text
   uv run --no-config --no-project --python 3.15 python <plugin-root>/scripts/launch.py agent_toolkit/wait_ci.py --write-baseline <手順1の領域>/<更新refごとに一意なファイル名>.json
   ```

baseline作成と監視では`--repo`（`owner/repo`かホストを含むURL。ローカルパスではない）、`--forge <github|gitlab>`、`--ref`、`--source-ref`をすべて指定する。その他の引数は`--help`で確かめる。複数refでは組ごとにbaselineを作成し、1件が失敗しても他を続ける。

GitLabのSSH接続先とAPI接続先が異なる場合は、API側のURLをポート込みで`--repo`へ渡す。
baseline作成と監視・再開では同じ指定を使う。
ホストを省略した`group/project`では、glabの公開環境指定（`GITLAB_HOST`・`GITLAB_URI`・`GITLAB_URL`）と通常の設定解決を利用する。

## pushと監視

1. 引数なし方式では`git push`を単独で実行し、明示方式では成功したdry-runから`--dry-run --porcelain`だけを除いた同じ引数でpushする
2. push成功後、各baselineに対して`wait_ci.py --baseline`を実行する（CI通過をこのセッションで判定しない場合は実行せず後始末へ進む）
   - 起動と結果回収は`agent-toolkit:delegation`の`references/waiting-and-monitoring.md`「待機区間の構成」に従う。完了通知で同じセッションへ再開する監視は背景で起動し、別の独立待機より先に届いた通知を処理する。同じbaselineの起動済み監視を回収し、重複起動しない
   - ホストから背景監視の停止通知を受けた場合は、同スキルの`references/wait-anomalies.md`「ホストによる背景待機の停止後」に従う。元の監視プロセスと出力を回収し、観測の再開には同じSHAとbaselineまたは待機専用記録を使う。実ジョブの再起動は含めない
   - 総待機時間は、GitHub Actionsでは対象workflowの直近の成功runの`updatedAt - startedAt`へ登録猶予と変動分の余裕を加えて決め、取得・算出できなければ270秒とする。`gh`の入出力形式は実行直前のヘルプで確かめる
   - 終了コード6と2では同じ引数で呼び直し、それ以外を待機の終端とする
   - 背景実行では、ホストがコマンドを時間で停止する上限を総待機時間より長くする。先に止められるとスクリプトは判定結果を返せなくなる。実行ハンドルのyield・再開がある場合は60秒未満の間隔で同一processへ再接続し、processを短い`--timeout`へ分割したりplugin root更新を契機に置き換えたりしない
   - push前のbaselineが無い場合（公開後のRelease workflowの完了確認を含む）と別の主体がpushしたcommitを待つ場合は、40桁の完全長SHAを`--wait-sha`へ、managed-temp内の待機専用JSONを`--failure-record`へ渡す。この形は対象SHAの全実行を判定し、baseline方式は自身のpushで新しく登録された実行だけを判定する
   - GitLabでは同一projectのbridgeが再帰的に指すdownstream pipelineとジョブも判定対象とし、別projectのdownstreamは対象外とする
3. 全対象が終了コード0で完了した場合だけCI通過とする。出力が空か成功完了マーカーが無ければ未判定として直接確かめる。判定対象はbaselineに保存したSHAの実行であり、push後にsource refが進んでも同じSHAで判定する。登録猶予後に登録された実行も含む（猶予は実行が1件も登録されない場合を区別するための待機であり、判定対象の期限とは別物である）。GitHubで除外するのは`event`が`dynamic`かつworkflow名が`Dependabot Updates`の実行だけとする
4. 終了コード6を受けたら`agent-toolkit:bugfix`を起動し、並行して同じbaseline・引数で呼び直す。終了コード1も同スキルで対処する
5. 修正では元のbaselineの待機が終端した出力の失敗集合全体を範囲に含めてから再pushする（失敗集合を取得できなければその原因を先に解消する）。
   再pushごとに新しいbaselineを作成して再監視し、失敗したjobが新しいpushの判定対象に含まれるかを確かめる。
   含まれない場合は`agent-toolkit:bugfix`の`references/ci-failure-handling.md`「修正commitが必要なCI失敗を修正するエージェント」の修正系列の定義に従う。修正方法は次の2区分から選ぶ
   - 原因commitへ取り込む区分: 次の全てが成立する場合は、修正を原因commitへ取り込む（amendか、fixupとautosquash）。
     更新は`git push --force-with-lease=<destination ref>:<書き換え前に観測したremote側のOID>`のように期待値を明示して行う（期待値を省くと、背景の`git fetch`で追跡refが進んだ時点で保護が無効になる）。
     実行手順は`references/history-rewrite.md`の「fixupの実行上の制約」「操作前後の確認」「失敗時の扱い」に従う
     - push先branchが、remoteのHEADが指すbranch（`git ls-remote --symref <remote> HEAD`）と異なる
     - push先branchが、対象リポジトリの規範（`AGENTS.md`など）が直接pushかforce pushを禁じるbranchではない
     - 操作直前の`git fetch --all --prune`後、書き換える最古のcommitに対する`git for-each-ref --contains=<そのcommit> refs/heads/ refs/remotes/`の出力がpush先のlocal branchとその追跡refだけである
   - 通常commitを使う区分: 前項のいずれかが成立しない場合（ベースbranchへ直接pushする運用を含む）は、同じbranchへ通常commitを追加し、force pushを避ける。forgeの保護設定でforce pushが拒否された場合も通常commitで修正し直す
   - 取り込む区分でも`## push前`のdry-runを同じ明示形で実行し、強制更新のstatus lineは3条件を満たす承認済みdestinationへの更新である場合だけ承認済みとする。CI記録には書き換え前後のOID、tree・親・件名の対応、`git range-diff`の結果と是正した原因の対応を残す
6. 診断のために対象jobを再実行した後も、同じbaselineで`wait_ci.py --baseline`を再起動して待機を任せる（自作の待機ループはbaselineの判定対象を再現できないため避ける）。許容された再実行後も失敗が残る場合はCI未通過を終端状態とし、未通過と帰属判定を完了報告、採否記録、`atk run-script plan-progress --`で計画の`## 進捗ログ`へ記録する。対象workflowが同一concurrency groupで`cancel-in-progress: true`の場合、再実行の終端前に新しくpushすると先行runが`cancelled`になり判定根拠を失うため、終端を確定してから次のpushを行う

`--baseline`による判定が終わるまで、同じコミットに対する`gh workflow run`などの手動起動は待つ。`wait_ci.py`はbaselineに無い同一コミットの実行も判定対象に含めるため、手動起動の失敗がCI失敗として返る。リリースや配備のworkflowはCI通過の確定後に起動する。

`wait_ci.py`の終了コードは次のとおり。

| 終了コード | 意味 |
| --- | --- |
| 0 | CI通過 |
| 1 | 全run終端・CI失敗。既報分を含む全失敗集合を出力 |
| 2 | timeout |
| 3 | forge CLIまたは対象判別の失敗 |
| 4 | run未登録 |
| 5 | CI定義なしのため監視対象なし |
| 6 | 未報告の失敗を検出し、未終端のrun・jobが残る。同じ引数で待機を再開 |
| 130 | 中断 |

## 後始末

CI成功、CI定義なし、CI判定の委譲、バグ対応完了、push失敗、監視不能、run未登録、forge CLI失敗、中断を終端状態とする。
監視の終端と公開工程の成功は分けて扱う。
`agent-toolkit:process-wi`の終端担当は、検収できた`CI成功`か`CI定義なし`を正常完了の`CIの結果`へ返す。
それ以外の未完了は既存の`続行できない理由:`で返し、CI照会不能と公開後の操作の前提不足では次節の情報も含める。
追加push（原因commitへ取り込んだ修正のforce pushを含む）では新しいディレクトリとbaselineを作成する。

## 公開工程の続行不能の受渡し

定義判定、baseline作成、push後の監視または公開後の操作を前提不足で完了できない場合は、同じ対象の確認済み工程と残る工程を分けて引き継ぐ。
対象forge・repository・commit・ref、失敗操作とその対象（release名、tag、配布先など）、診断、成功済みの公開後の操作、pushの成否、公開状態の4項目の観測を記録する。
全体検証の成否、版数・生成物・公開後の操作・延期adoptの到達点も記録し、CI結果を保持し、未判定の場合はその旨を明示する。
baselineと保存出力、引き継ぎ記録の絶対パス、欠ける前提と再開工程を渡す。
push前に止まった場合はpush未実行、push後なら確認済みpushを保持する。
公開状態の4項目やローカル全体検証の成功だけでCI成功にはしない。

回復とユーザー確認は`references/publish.md`「公開工程の続行不能からの再開」に従う。
CI判定の回復後は保存済みbaselineか「pushと監視」の`--wait-sha`を使う。公開後の操作は記録した対象と再開位置を使い、同じcommitの未完了工程だけを再開し、成功済みの版数更新・push・公開後の操作を繰り返さない。
