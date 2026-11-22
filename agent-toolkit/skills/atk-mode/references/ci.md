# CIの判定・待機・失敗の扱い

公開先のCI定義の有無の判定、`wait_ci.py`（`atk run-script wait-ci`）による待機、CIの失敗の証拠の取得と修正の公開を定める。
`agent-toolkit:atk-mode/playbooks/publish.md`の「push前」「pushと監視」の段から使う。
CIの失敗を認識したら`agent-basics:bugfix`を起動し、失敗の分類と対処は同スキルの`references/ci-failures.md`に従う。
修正commitを要するCI失敗の担当と修正系列は、`agent-toolkit:atk-mode/playbooks/process-wi-ci-fix.md`に従う。

## 公開先に対応するCI定義の判定

承認済みのpush先forge、リポジトリ（ホスト名を含む）、対象commitを入力として、その公開先で実行されるCI定義の有無を判定する。
結果を記録する側と受け取る側は本節の基準を共通に使い、候補は公開先forgeのCI定義に限る。
公開先と更新するrefの組ごとに、対象commitのツリーを証拠として判定する。

| 公開先 | 判定する定義と不在の根拠 |
| --- | --- |
| GitHub | 対象commitの`.github/workflows`の直下にある、拡張子が`.yml`か`.yaml`のblobを候補とする。`git ls-tree -r <対象commit> -- .github/workflows`でオブジェクトの種類とパスを確かめ、終了コード0で候補が0件なら不在とする。READMEやサブディレクトリの中のYAML、ツリーオブジェクトは候補に含めない |
| GitLab | 対象プロジェクトの有効な設定が指すCI設定ファイルと、Auto DevOpsの状態を判定する。標準の`.gitlab-ci.yml`に加え、独自の`ci_config_path`が指す同じプロジェクト・別プロジェクト・外部URLの設定と、継承を含むAuto DevOpsの有効状態を確かめる。有効な定義が無く、Auto DevOpsも無効と確定した時だけ不在とする |

GitLabの設定は[Projects API](https://docs.gitlab.com/api/projects/)と[独自CI設定の仕様](https://docs.gitlab.com/ci/pipelines/settings/#specify-a-custom-cicd-configuration-file)で取得する。
応答に権限不足による項目の欠落が無いことと、継承した[Auto DevOps](https://docs.gitlab.com/topics/autodevops/)が有効でないことを確かめる。

判定の結果は次のどれかとし、記録には公開先、対象commit、取得したツリーか有効な設定と参照先、実行したコマンド、終了コード、診断を残す。

- CI定義なし: 不在を確定した。
  baselineと監視を省き、「CI定義不在」の証拠とローカル全体検証の成功を組にして最終状態とする。
- CI実行対象外: CI定義はあるが、対象commitとrefのpipelineが生成されないと確定した（`wait_ci.py`の終了コード8）。
  出力されたproject、commit、ref、errorsを保存し、ローカル全体検証の成功と組にして受理する。
  同じ内容のローカル全体検証が未実施なら、タスクランナーで実行してから受理する。
  CI通過とCI定義なしのどちらとも区別する。
- run未登録: CI定義があり、実行の条件を満たすかが確定しないままrunが登録されない（終了コード4）。
- 未判定: 取得の失敗、権限不足、応答の切り詰め、設定項目の不足、独自設定の参照先の取得の失敗により、候補が未確定の状態である。
  CI成功や定義不在とみなさず、`agent-toolkit:atk-mode/playbooks/publish.md`「公開工程の続行不能の受渡し」へ渡して、再判定かCIの照会の復旧を図る。

## CIの待機

`wait_ci.py`はpush前に保存したrunのIDのbaselineとの差分で、自分のpushが登録したrunを待つ。
受理する引数は`atk run-script wait-ci -- --help`で確かめる。

### baselineの作成

1. セッションのmanaged-temp（`agent-toolkit:atk-mode/playbooks/managed-temp.md`「作業用ディレクトリ」）の中に、pushごとに新しいディレクトリを作る。
   追加のpush（原因commitへ取り込んだ修正のforce pushを含む）のたびに、新しいディレクトリとbaselineを作る。
2. 削除するrefを除き、更新するrefごとに、確定したrefspecの左辺`<source>`をそのまま`--source-ref`へ渡す。
   右辺の宛先のrefやリモート追跡ブランチの値とは別の値である。
3. pushに使う作業ツリーで、push先リポジトリと `(destination ref, source ref)` の組ごとに次を実行する。

   ```text
   atk run-script wait-ci -- --repo <owner/repo> --forge github --ref <destination-ref> --source-ref <source-ref> \
     --write-baseline <手順1のディレクトリ>/<更新refごとに一意なファイル名>.json
   ```

   ローカルでSHAを解決する場合は、現在の作業ツリーに `--repo` と一致するpush先が必要になる。
   GitLabの接続先が設定ファイルからしか分からない場合は、`--repo` にホスト名を含める。
   別の作業ツリーから起動する場合は、pushする作業ツリーで解決した完全長SHAを `--sha` で指定する。
   1件の作成が失敗しても、残りの作成を続ける。

`--write-baseline`と`--wait-sha`では、`--repo`（ローカルパスではなく`owner/repo`かホスト名を含むURL）、`--forge <github|gitlab>`、`--ref`、`--source-ref`を全て明示する。
公開手順以外では、`--forge` の省略時に、ホスト付きの `--repo`、または同じプロジェクトパスに一致するGit remoteとリポジトリ登録の一意なホストからforgeを判定する。
候補がない場合や複数ホストが一致する場合は、ホスト付きの `--repo` を指定する。
ホスト名からforgeを判定できない場合は `--forge` も明示する。
自動判定したホストは待機記録へ保存する。
短縮形の再入力は、自動判定でその短縮形から作成した記録だけが受理する。
GitLabでは、Gitの転送方式やポートではなくglabのホスト別API設定を使う。
自動判定したGitLabホストと環境変数のホスト指定が一致する場合だけ、環境変数のAPI接続設定を引き継ぐ。
GitLab CIの自動ログインは、CIのホストが自動判定したGitLabホストと一致する場合だけ引き継ぐ。

`wait_ci.py`は`--ref`で指定した宛先のブランチかタグの短縮名を、GitHubでは`gh run list --branch`へ、GitLabでは`glab ci list --ref`へ渡す。
`--baseline`での待機と再起動はbaselineの保存値を使うため、これらを省く。
`--forge gitlab`を明示して`--repo`のホストを省いた場合は、`GITLAB_HOST`、`GITLAB_URI`、`GL_HOST`の順に使い、どれも設定していなければglabの設定を使う。
GitLabでは、`GITLAB_API_HOST`をリポジトリの識別子やSSHの接続先から上書きせず、baselineの作成と監視・再開で同じ接続の設定を使う。

### 待機の起動と回収

push後は、baselineごとに `atk run-script wait-ci -- --baseline <baselineの絶対パス> --sha <実際にpushしたSHA>` を実行する。
push前のbaselineが無い場合と、他の主体がpushしたcommitを待つ場合は、40桁の完全長SHAを`--wait-sha`へ、managed-tempの中の待機専用のJSONを`--failure-record`へ渡す。
この形式は対象SHAの全てのrunを判定する。

```sh
atk run-script wait-ci -- --wait-sha <40桁のSHA> --failure-record <待機専用JSONの絶対パス> --repo <owner/repo> --forge github --ref <destination-ref> --source-ref <source-ref>
```

- 起動と結果の回収は`agent-toolkit:atk-mode/playbooks/delegation-waiting.md`「待機区間の構成」に従う。
  背景で起動し、他の独立した待機より先に届いた通知から回収する。
  同じbaselineの監視が起動済みなら、新しく起動せずにそのプロセスを回収する。
- ホストが背景の監視を停止した場合は、`agent-toolkit:atk-mode/references/delegation-wait-anomalies.md`「ホストによる背景待機の停止後」に従う。
  CIのジョブは再実行せず、停止した監視と出力を回収して、同じSHAとbaseline（か待機専用の記録）で監視を再開する。
- `--timeout`は判定するrunと同じジョブ構成（実行するジョブ、パスの条件によるスキップ、マトリックス、軽量実行の有無）を持つ過去の成功runの所要に、登録の猶予と変動の余裕を足した有限の値にする。
  GitHub Actionsの所要は`updatedAt - startedAt`で、`gh run view <run ID> --json startedAt,updatedAt,jobs`などでジョブ構成と合わせて確かめる。
  直近の成功runが軽量実行か一部のジョブだけなら、通常の作業量を含む成功runまで遡る。
  所要を取得できない時は`--timeout`を省き、省略時の270秒を使う。
- ホストが背景のコマンドを止める上限は、`--timeout`より長くする（ホストが先に止めると判定の結果が失われる）。
  見積もった総待機がホストの背景実行の最大（Claude Codeの`Bash`では7,200,000ミリ秒）を超える時は、最大より短い`--timeout`で起動し、終了コード2を受けてから同じ対象を待ち直す。
- Codexでは`agent-toolkit:atk-mode/playbooks/host-codex.md`「agents_serverの二層待機」に従い、未起動の監視を通知で再開できる小さい担当へ渡す案と、元の担当での二層の回収を総費用で選ぶ。
  起動済みの監視は、短い`--timeout`へ分割したり、プラグインのrootの更新を契機に別プロセスへ置き換えたりせず、同じ実行ハンドルで回収する。
- `--baseline`による判定が終わるまで、同じcommitのワークフローを`gh workflow run`などで手動起動しない。
  `wait_ci.py`はbaselineに無い同じcommitのrunも監視に含めるため、手動起動したrunの失敗がCI全体の失敗になる。
- 対象のワークフローが同じconcurrency groupで`cancel-in-progress: true`なら、先行のrunの完了を確かめてから次のpushをする。
  完了の前にpushすると、先行のrunが`cancelled`になって判定の証拠を失う。
- GitLabでは、同じプロジェクトのbridgeが再帰的に起動する下流パイプラインとジョブも判定に含め、別プロジェクトの下流パイプラインは対象の外とする。

### CI通過の判定

- 全ての監視対象が終了コード0で終わった時だけ「CI通過」とする。
  出力が空の時と、成功の完了を示す表示が無い時は、未判定として状態を直接確かめる。
- 判定の対象はbaselineに保存したcommit SHAのrunであり、push後に送信元のブランチが進んでも同じSHAで判定する。
- 成功した時も、対象プロジェクトが定める段階別と全体の所要の基準を読んで実際の所要と比べ、既存のCIの検証記録へ残す。
  比べ方と超過した時の改善の判断は`agent-basics:check-execution/references/diagnostics.md`「読む出力」に従う。
  所要の基準と監視の`--timeout`は別のものであり、所要の超過は改善の判断に使い、公開の停止と検証の省略の理由から外す。

### wait_ciの終了コード

0以外の終了では、`wait_ci.py`が標準エラーへ次の操作を示す。
次の表の「扱い」は、公開工程での扱いである。

| 終了コード | 意味 | 扱い |
| --- | --- | --- |
| 0 | CI通過 | 本節「CI通過の判定」で確かめる |
| 1 | 全てのrunが終わり、CIが失敗した。報告済みのものを含む失敗ジョブの全体を出力する | `agent-basics:bugfix`を起動し、本書「選択の流れ」へ進む |
| 2 | タイムアウト | 同じSHAとbaseline（か待機専用の記録）を保ち、ジョブの進み具合と経過時間から残りを見積もって`--timeout`を延ばし、呼び直す |
| 3 | forge CLIの実行の失敗か、対象のリポジトリ・refの特定の失敗 | 私設GitLabなら本書「私設GitLabのCLI失敗からの回復」へ進み、それ以外は待機を終えて「forge CLI失敗」とする |
| 4 | runが未登録 | 待機を終えて「run未登録」とする |
| 5 | CI定義が無く、監視の対象が無い | 待機を終えて「CI定義なし」とする |
| 6 | 未報告の新しい失敗を検出し、実行中のrunかjobが残っている | `agent-basics:bugfix`を起動し、並行して同じbaselineと引数で呼び直す |
| 7 | コマンドの引数の誤り | `atk run-script wait-ci -- --help`で受理形式を確かめて引数を直し、起動し直す |
| 8 | GitLab CI Lintで、対象commit・refのpipelineの非生成を確定した（CI実行対象外） | 本書「公開先に対応するCI定義の判定」の「CI実行対象外」として扱う |
| 130 | 処理の中断 | 待機を終えて「中断」とする |

## 私設GitLabのCLI失敗からの回復

1. `wait_ci.py`がCLIの失敗を示す終了コード3を返した時だけ、状態の照会と証拠の取得のために`curl -k`でAPIを直接呼ぶ。
   待機の対象ホストの設定からAPIのベースURLを確認し、下の例の`API_BASE_URL`に設定する。
   シェルの`GITLAB_HOST`や`GITLAB_TOKEN`をそのまま流用せず、対象API用のトークンであることを確かめる。

   ```text
   curl -k -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
     "${API_BASE_URL}/api/v4/projects/<project-id>/pipelines?sha=<完全SHA>&per_page=100&page=<page>"
   curl -k -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
     "${API_BASE_URL}/api/v4/projects/<project-id>/pipelines/<pipeline-id>/jobs?include_retried=false&per_page=100&page=<page>"
   ```

   パイプラインの一覧と、パイプラインごとのジョブの一覧は、返る件数が100件未満になるまで`page`を増やして全てのページを取得する。
2. 取得の後は本書「CIの待機」へ戻り、同じ完了の条件と終了状態で判定する。

## 失敗ログとartifactの取得

- 監視で得たrun ID（GitLabではpipeline ID）とjob IDを引き継ぎ、全体の実行状態を確かめてから、失敗ジョブのログを取得する。
  `wait_ci.py`が示す次の操作に、対象に合うログ取得のコマンドがある。
  `gh`と`glab`の受理形式は、実行の直前にヘルプで確かめる。
- GitHubでは、ジョブごとに`gh api repos/<OWNER>/<REPO>/actions/jobs/<job ID>/logs --allow-escape-sequences`で全てのログをmanaged-tempへ保存する。
  端末のエスケープシーケンスを含む応答は、このオプションが無いと取得を拒否される。
  run全体の終了後は、`gh run view <run ID> --repo <OWNER>/<REPO> --json jobs`で失敗した全てのジョブのIDを確かめ、同じAPIで各ジョブのログを保存する。
- `gh run view --log-failed`は64KiBを超える長い行（巨大なパラメーターを含むテスト名など）があると、それ以降の出力を欠いたまま終了コード0で終わることがある。
  このため、失敗ログの取得と失敗の有無の判定の手段から外す。
- run全体の進行中に完了した失敗ジョブは、先にログを取得して分析を始める。
  ログが取れない時とジョブが進行中の時は、アノテーションや失敗したテスト名など取れる証拠で先に分析する。
  失敗ジョブの全体は、`wait_ci.py`を呼び直して全体の完了の結果を受け取ってから確かめる（終了コードごとの扱いは本書「wait_ciの終了コード」）。
- VRTの差分画像やtest-resultsなどのartifactも取得する。

状態ごとのログの取得を確かめた記録は、`docs/development/audit-records-commit-ci.md`の次の節にある。

- 「agent-toolkit/skills/atk-mode/references/ci.md：GitHubの状態別ログ取得：2026年10月1日」
- 「agent-toolkit/skills/atk-mode/references/ci.md：GitHubの状態別ログ取得：2026年10月24日」

## 選択の流れ

1. 原因を取り違えないため、本書「失敗ログとartifactの取得」で証拠を取得する。
   非決定的な失敗の兆候による対象ジョブの再実行と、取れる証拠の暫定の保存は、plan modeの開始の前に行う。
   修正や再投入の前に、`agent-basics:bugfix`を起動済みであることを確かめる。
2. 失敗したジョブの全体を確かめて修正の範囲を確定する。
   分類の結果（VRTでは差分の分類を含む）は既存のCIの記録か進捗へ記し、計画を作る場合は計画の調査の工程へ記す。
   `agent-toolkit:atk-mode`の作業`process-wi`の公開工程では、公開工程が管理する原因分析の結果へ記す。
3. 環境要因か、自セッションの変更と無関係と分類した場合は、`atk wi list --type=awi --status=active`で同じ事象を扱うAWIを探す。
   該当があれば`atk wi show <ファイル名> --skip-pull`で本文の緩和策を読み、`agent-basics:bugfix/references/ci-failures.md`「対処」の選択肢と比べる。
4. 選んだ対処、baselineなどの基準の情報、CIの生の観測、分析の結果を引き継ぐ。
   公開工程では、公開担当がこれらをCIの記録として保持し、CI修正担当へ渡す（`agent-toolkit:atk-mode/playbooks/process-wi-ci-fix.md`）。
5. 分かっている修正は全てまとめて、1回のpushで公開する。
   元のbaselineの待機が終わった時点の出力にある全ての失敗ジョブを修正の範囲に含める。
   失敗の全体を取得できない時は、先にその原因を除く。
6. 再pushのたびに新しいbaselineで監視し、前回失敗したジョブが新しいpushの監視の対象に含まれることを確かめる。
   含まれない場合は、`agent-toolkit:atk-mode/playbooks/process-wi-ci-fix.md`「修正commitが必要なCI失敗を修正するエージェント」の修正系列の定めに従う。
7. 原因の調査でCIのジョブを手動で再実行した場合も、同じbaselineで`atk run-script wait-ci -- --baseline <baselineの絶対パス>`を呼び直し、baselineに基づく判定を再現できるこのスクリプトに監視を任せる。
   許された再実行でも失敗が解消しない場合は最終状態を「CI未通過」とし、その事実と帰属の判定を、完了の報告、採否の記録、`atk run-script plan-progress --`による計画の進捗ログに記す。

### 原因commitへ取り込む区分

修正は次の3条件が全て成り立つ時だけ原因commitへ取り込む（amend、またはfixupとautosquash）。
操作は`agent-toolkit:atk-mode/playbooks/history-rewrite.md`に従う。

- push先のブランチが、リモートのHEADが指すブランチ（`git ls-remote --symref <remote> HEAD`で確かめる）と異なる。
- push先のブランチが、対象リポジトリの規範（`AGENTS.md`など）が直接のpushかforce pushを禁じるブランチと異なる。
- 操作の直前の`git fetch --all --prune`の後、`git for-each-ref --contains=<書き換える最古のcommit> refs/heads/ refs/remotes/`の出力が、push先のローカルブランチとその追跡ブランチだけである。

リモートは`git push --force-with-lease=<destination ref>:<書き換え前に観測したremote側のOID>`のように、期待するOIDを明示して更新する。
期待値を省くと、背景の`git fetch`などでリモート追跡ブランチが進んだ時に、上書きの保護が失われる。
この場合も`agent-toolkit:atk-mode/playbooks/publish.md`「push前」のdry-runを同じ明示の形式で実行し、強制更新を示すstatus lineが3条件を満たす承認済みの宛先への更新である時だけ承認済みとして扱う。
3条件のどれかが成り立たない場合（ベースブランチへ直接pushする運用を含む）と、forgeのブランチ保護がforce pushを拒んだ場合は、force pushを使わずに、同じブランチへ通常のcommitを足して直す。
CIの記録には、書き換え前後のOID、ツリー・親commit・件名の対応、`git range-diff`の結果、解消した原因との対応を残す。
