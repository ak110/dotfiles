# pushとCI通過確認

push主体がpush先、更新ref、基準情報、CI監視、
証拠用一時領域のライフサイクルを所有する。通常commit、stage、messageは親スキル、
CI失敗の帰属と原因分析は`../../bugfix/SKILL.md`に従う。

## リリースバージョン指定

プロジェクト方針が無い場合は次の基準を用いる。

- ユーザーが明示したバージョン区分（MAJOR、MINOR、PATCH）を最優先とする
- MAJORリリースはユーザーの明示指示がある場合に限る。MAJORは互換破壊の外部宣言であり、`agent-toolkit:user-confirmation-and-report`の`references/judgment.md`「認可を要する操作」が定める承認対象に当たる
- 現行版が数値3要素のSemVerでない場合は、プロジェクトの対応表またはユーザーが明示した区分から区分を決める。いずれも無い場合はバージョンを変更せず、判定不能の根拠を報告する。文字列の辞書順と桁数は区分の判定材料から外れる

## ローカルで実行するlintとCIジョブの対応

push前に対象プロジェクトのCI定義を読み、ローカルで実行した全体検証が対応するジョブと、ローカルでは実行されないジョブを確定する。
別のOS、別の言語バージョン、実機に依存する資源などが、ローカルでは実行されないジョブが検証する条件に当たる。
変更対象がこの条件を含む場合は、条件をローカルで検証できる形へ変えてからpushすることを推奨する。
ローカルで検証できる形は失敗をpush前に見つけられる一方、形を変える費用が便益を上回る場合もある。形を変える費用と、CIの結果を待つ場合の所要時間と手戻りを比べて選ぶ。
形を変えない場合は、CIの結果を待つ工程を見込む。

## 公開状態の4項目

ベースbranchの公開を確かめる次の4項目を「公開状態の4項目」と呼ぶ。これらは判定の時点で現在のGit状態から再取得し、全て成立した場合だけ公開済みと判定する。これらの取得には、`.git`内部のパス探索と複数のrefを1回へ渡す`git rev-parse --short`を使わない。

1. `git -C <対象リポジトリの絶対パス> status --porcelain=v2 --branch`の`# branch.head`の値がベースbranch名と一致する
2. 同じ出力で`#`で始まらない行が0件である
3. 同じ出力の`# branch.ab`のahead値が`+0`である。追跡refが無く`# branch.ab`を取得できない場合は不成立とする
4. `git -C <対象リポジトリの絶対パス> status`にrebase・merge・cherry-pickの進行中を示す表示が無い

## push前

直前に`git commit --amend`または`git commit --fixup`を実行した作業ツリーでは、pushの前に`git status --short`を単独で実行し、追跡ファイルの未コミット差分が残っていないことを確認する。差分が残る場合はその差分を確定してからpushへ進む。

1. pushの許可（計画に記録した元の要求または確認回答・委譲元の委譲プロンプト・ユーザー指示のいずれか）が
   対象リポジトリと対象branchを含むことを確認する
2. `git fetch`後に上流との差分を双方向で確認する。上流が進んでいる場合は追随後に検証をやり直す
3. `git remote -v`、`git branch --show-current`、追跡branch、有効な`push.default`と明示された承認済みdestinationから、引数なしpushの到達先を先に判定する。`push.default=simple`で現在branch名と追跡branch名が異なる場合や追跡branchが無い場合など、引数なしpushの失敗が確定する構成では、そのdry-runを省く。承認済みの`<remote> <source>:refs/heads/<destination>`を明示した`git push --dry-run --porcelain`を最初に試す。引数なしpushが承認済みdestinationへ到達すると確定する場合は、引数なしdry-runを最初に実行する。設定だけで判定できない場合は、引数なしdry-runを試し、失敗するか意図したrefspecを示さなければ明示dry-runを続ける。

   成功したdry-runの全status lineが承認済みremote・destinationへのrefspecを示す場合だけ、その方式を選ぶ。拒否や失敗予定のref、または承認範囲と異なるremote・destinationがあればpushしない。明示指定ではremote、source、完全なdestination refをすべて書き、実際のpushも成功したdry-runと同じ方式を使う

CIを判定する場合は、pushするcommitのtreeでCI定義の有無を`git -C <対象リポジトリの絶対パス> ls-tree -r --name-only <pushするcommit> -- .gitlab-ci.yml .github/workflows`で判定する。forgeによらず同じ判定を使う。終了コード0で出力が0行の場合はCI定義が無いため、次の3工程と「pushと監視」のbaselineによる監視を省き、push結果を判定した後の終端状態を「CI定義なし」とする。終了コードが0以外の場合は原因を確かめてから判定し直す。

委譲元がそのpushのCI通過をこのセッションで判定しないと明示した場合は、次の3工程を省き、「pushと監視」のpush結果判定へ進む。
CIを判定する場合は、次の3工程で監視用の証拠を作成する。

1. セッションのmanaged-temp（`agent-toolkit:managed-temp`）の中へ、pushごとに別のディレクトリを作成し、その絶対パスを保持する
2. 削除refを除き、更新refごとにsource refを1件確定する。
   手順3で確定したrefspecの左辺`<source>`を、そのままbaselineの`--source-ref`へ渡す。
   `--source-ref`へ渡すのはこの左辺だけとし、refspecの右辺`<destination>`、destination ref、remote-tracking refは別の値として扱う。
   baseline作成時に補助スクリプトがsource refをcommitへ再帰的にpeelし、完全長commit SHAを保存する
   - annotated tagとlightweight tagのどちらでもraw tag OIDではなくpeeledしたcommit SHAを保存する
   - pushできるのはcommitへpeelできるrefに限る。peelできないrefではbaseline作成が失敗する
   - GitHubではpush workflowのSHAが更新refのtipであり、GitLabではpipelineがcommit単位ではなくpush単位で起動する
3. 読み込んだ本文書の絶対パスからplugin rootを確定する。
   確定した各`(destination ref, source ref)`について、push前に`uv run --project <plugin-root> --locked --no-default-groups <plugin-root>/agent_toolkit/wait_ci.py`を
   `--write-baseline <この節の第1工程で保持した領域の絶対パス>/<呼び出し側が更新refごとに決めた一意なファイル名>.json`付きで実行し、baseline JSONを保存する。
   `wait_ci.py`は直前のコマンドで確定したパスにあり、
   `<plugin-root>/skills/commit/scripts/`には無い

CIを判定する場合はbaseline作成、push、監視の順で実行する。
baseline作成と監視では`--repo`、`--forge`、`--ref`、`--source-ref`をすべて指定する。
`--repo`にはリポジトリ識別子（`owner/repo`、またはホストを含むURL）を渡す。作業ツリーなどのローカルパスは別の入力として扱う。
引数の詳細は`--help`で確認する。
単一refと複数refのいずれでも、GitHubとGitLabの両方で、選定済みforgeを`--forge <github|gitlab>`へ明示する。
複数refでは組ごとに別のbaselineを作成し、1件が失敗した場合も他のbaselineの作成と監視を続ける。

## pushと監視

1. 標準指定ではremote名とbranch名を明示せず`git push`を単独で実行する。
   明示指定では、成功したdry-runから`--dry-run --porcelain`だけを除いた同一の`<remote> <source>:<destination>`を渡す
2. push成功後、保存した各baselineに対して同スクリプトを`--baseline`付きで実行する
   - CI通過をこのセッションで判定しない場合は、`--baseline`を実行せずに後始末へ進む
   - GitHub Actionsでは、対象workflowの直近の成功runから開始時刻と終了時刻を取得する。`gh`の入力と出力形式は実行直前のヘルプで確定する
   - `updatedAt - startedAt`の実績へ登録猶予と変動分の余裕を加えて総待機時間を決める
   - 対象workflowの成功runが取得できない場合と実績を算出できない場合は270秒を使う
   - baselineごとに確定した総待機時間を指定して1回だけ起動する
   - 背景実行で起動する場合は、ホストのツールがコマンドを時間で停止する上限を総待機時間より長くする。引数を省略した場合の上限と指定できる最大値はホストのツール説明が示す値を使い、省略した場合の上限が総待機時間より長ければその引数を省略し、短ければ総待機時間より長い値を指定する。総待機時間より先にホストが止めると、スクリプトは判定結果の終了コードを返せず、同じbaselineでの待ち直しが要る
   - ホストが実行ハンドルのyield・再開を提供する場合は、60秒未満の観測間隔で同一processへ再接続する
   - 起動した処理は同じprocessのまま維持する。進捗表示のために短い`--timeout`の別processへ分割する形と、実行中のplugin root更新を契機に置換する形は、いずれも判定対象の実行を取りこぼす
   - push前のbaselineが無い場合または別の主体がpushしたcommitを待つ場合は、対象の40桁の完全長commit SHAを
     `--wait-sha`へ渡す。この起動形は対象SHAの全実行を判定対象とする
   - baseline方式はpush前に存在した実行IDを除外するため、自身のpushにより新しく登録された実行だけを判定対象とする
   - GitLabでは、親pipelineに加えて同一projectのbridgeが再帰的に指すdownstream pipelineとそのジョブを判定対象とし、入れ子の下流も親の待機結果へ反映する
   - 別projectのdownstream pipelineは対象外とする
3. CIを判定する場合は、全対象が終了コード0で完了した場合だけCI通過と判定する。
   終了コードの意味は後掲の表に従う。
   出力が空の場合や成功完了マーカーが無い場合は未判定として直接の確認へ切り替える。
   判定対象はbaselineへ保存した完全長SHAに対する実行とし、source refがpush後に進んだ場合も同じSHAで判定する。
   GitHubでpushへ帰属しない自動更新として除外するのは、`event`が`dynamic`かつworkflow名が`Dependabot Updates`である実行に限る。
   同名workflowの手動実行と、他workflowの`dynamic`実行は判定対象へ含める。
   登録猶予の終了後に登録された実行も判定対象に含む。
   登録猶予は、実行が1件も登録されないまま終わる場合を区別するための待機であり、
   判定対象を確定する期限ではない
4. CI失敗では、最初の失敗jobを検出した時点で`agent-toolkit:bugfix`を起動し、監視は継続する。証拠の取得、帰属、原因および拡張原因分析の要否は同スキルのCI失敗分析契約に従う
5. CI失敗の修正方法を、push先と修正対象のcommitによって次の2区分から選ぶ。修正後はどちらの区分でも同じbranchへ再pushする。そのpush用の新しいbaselineを作成し、`wait_ci.py --baseline`で再監視する。再監視ではCI失敗を起こしたjobが新しいpushの判定対象に含まれるかを確かめる。含まれない場合の扱いは`agent-toolkit:bugfix`の`references/ci-failure-handling.md`「修正commitが必要なCI失敗の実施主体」の修正系列の定義に従う。
   - 原因commitへ取り込む区分: 次の全てが成立する場合は、修正を原因のcommitへ取り込む（amendか、fixupとautosquash）。同じbranchは`git push --force-with-lease=<destination ref>:<書き換え前に観測したremote側のOID>`のように期待値を明示した形で更新する。背景の`git fetch`で追跡refが進むと、期待値を省いた`--force-with-lease`の保護が働かない。取り込みの実行手順は`agent-toolkit:commit`の`references/history-rewrite.md`の「fixupの実行上の制約」「操作前後の確認」「失敗時の扱い」に従う
     - push先のbranchが、remoteのHEADが指すbranch（`git ls-remote --symref <remote> HEAD`が示すbranch）と異なる
     - push先のbranchが、対象リポジトリの規範（`AGENTS.md`など）が直接pushまたはforce pushを禁じるbranchに当たらない
     - 操作の直前に`git fetch --all --prune`を実行する。その後、書き換える最古のcommitに対する`git for-each-ref --contains=<そのcommit> refs/heads/ refs/remotes/`の出力が、push先のlocal branchとその追跡refだけである。他のbranch、worktreeが保持するbranch、別remoteから到達できる場合は書き換えない
   - 通常commitを使う区分: 前項のいずれかが成立しない場合は、同じbranchへの通常commitとして追加し、force pushを使わない。ベースbranchへ直接pushする運用はこの区分に当たる。forgeの保護設定でforce pushが拒否された場合も、push失敗として扱い、通常commitで修正し直す
   - 原因commitへ取り込む区分でも`## push前`手順3のdry-runを同じ明示形で実行する。強制更新を示すstatus lineは、前項の3条件を満たし承認済みdestinationへの更新である場合だけ承認済みとして扱う。CI記録には書き換え前後のOID、tree・親・件名の対応、`git range-diff`の結果と是正した原因の対応を残す
6. 診断目的で対象jobを再実行した後も、保存済みの同一baselineに対して`wait_ci.py --baseline`を再起動し、待機はこのスクリプトへ委ねる。自作の待機ループは、baselineが定める判定対象を再現しないため、待機手段として使わない。
   許容された再実行後も失敗が残る場合は、CI未通過を終端状態として確定する。
   完了報告と採否記録には、未通過であることと帰属判定を記録する。
   `atk run-script plan-progress --`で計画ファイルの`## 進捗ログ`へも同じ内容を記録する。
   対象workflowが同一concurrency groupで`cancel-in-progress: true`を有効にしている場合、
   再実行の終端前の新規pushは先行runを`cancelled`にし、非決定性とCI通過の判定根拠を失わせる。
   判定に用いるrunの終端を確定してから次のpushを行う

`--baseline`によるCI通過の判定が終わるまで、同じコミットに対する`gh workflow run`などのworkflow手動起動は待つ。
`wait_ci.py`はbaselineに無い同一コミットの実行も判定対象へ含めるため、手動起動したworkflowの失敗がCI失敗として返る。
リリース、配備その他のworkflowの手動起動は、CI通過を確定した後に行う。

`wait_ci.py`の終了コードは次のとおり。

| 終了コード | 意味 |
| --- | --- |
| 0 | CI通過 |
| 1 | CI失敗 |
| 2 | timeout |
| 3 | forge CLIまたは対象判別の失敗 |
| 4 | run未登録 |
| 5 | CI定義なしのため監視対象なし |
| 130 | 中断 |

## 後始末

CI成功、CI定義なし、CI判定の委譲、バグ対応完了、push失敗、監視不能、run未登録、forge CLI失敗、中断を終端状態とする。
`agent-toolkit:process-wi`の終端担当は、これらの終端状態の名前を返却の`CIの結果`の値に使う。
追加pushでは新しいディレクトリとbaselineを作成する。原因commitへ取り込んだ修正のforce pushにも同じく適用する。
