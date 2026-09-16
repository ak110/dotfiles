# pushとCI通過確認

## リリースバージョン指定

プロジェクト方針が無い場合は次の基準を用いる。

- ユーザーが明示したバージョン区分（MAJOR、MINOR、PATCH）を最優先とする
- MAJORリリースはユーザーの明示指示がある場合に限る。MAJORは互換破壊の外部宣言であり、`agent-toolkit/rules/01-agent.md`「協調と自律」の確認要否第2段階が定める承認対象に当たる
- 現行版が数値3要素のSemVerでない場合は、プロジェクトの対応表又はユーザーが明示した区分から区分を決める。いずれも無い場合はバージョンを変更せず、判定不能の根拠を報告する。文字列の辞書順と桁数は区分の判定材料から外れる

実際にpushする直前に本文書を全文読む。push主体がpush先、更新ref、基準情報、CI監視、
証拠用一時領域のライフサイクルを所有する。通常commit、stage、messageは親スキル、
CI失敗の帰属と原因分析は`../../bugfix/SKILL.md`を正本とする。

## ローカル検査とCIジョブの対応

push前に対象プロジェクトのCI定義を読み、ローカルで実行した全体検査が対応するジョブと、ローカルでは実行されないジョブを確定する。
別のOS、別の言語バージョン、実機に依存する資源などが、ローカルでは実行されないジョブが検証する条件に当たる。
変更対象がこの条件を含む場合は、条件をローカルで検証できる形へ変えてからpushする。
形を変えられない場合は、CIの結果を待つ工程を見込む。

## push前

直前に`git commit --amend`又は`git commit --fixup`を実行した作業ツリーでは、pushの前に`git status --short`を単独で実行し、追跡ファイルの未コミット差分が残っていないことを確認する。差分が残る場合はその差分を確定してからpushへ進む。

1. pushの許可（計画ファイルの確定事項・委譲元の起動文・ユーザー指示のいずれか）が
   対象リポジトリと対象branchを含むことを確認する
2. `git fetch`後に上流との差分を双方向で確認する。上流が進んでいる場合は追随後に検証をやり直す
3. `git remote -v`、`git branch --show-current`、有効なpush設定から、承認済みのremoteとdestinationを確認する。
   最初に引数なし`git push --dry-run --porcelain`を実行する。
   引数なし経路が失敗するか意図したrefspecを示さない場合は、
   `git push --dry-run --porcelain <remote> <source>:<destination>`を実行する。
   次の表で経路を選ぶ

   | 引数なしdry-runの結果 | 明示dry-runの結果 | 選ぶ経路 |
   | --- | --- | --- |
   | 成功し、全status lineが承認済みremote・destinationへの意図したrefspecを示す | 実行不要 | 標準経路 |
   | 失敗、または意図したrefspecを示さない | 成功し、remoteとdestinationが承認範囲と完全一致 | 明示経路 |
   | 失敗、または意図したrefspecを示さない | 上記以外 | pushしない |

   明示経路ではremote、source、完全なdestination refをすべて書く。
   pushへ進むのは、いずれの経路でも拒否と失敗予定のrefが無い場合に限る
4. SessionStartが管理対象一時領域を通知している場合は、
   `atk managed-temp create --prefix ci-evidence --session-root <通知された絶対パス>`を単独で実行する。
   通知が無い場合は`atk managed-temp create --prefix ci-evidence`を単独で実行する。
   標準出力の絶対パスと独立登録の有無を保持し、pushごとに別の領域を使う
5. 削除refを除き、更新refごとにsource refを1件確定する。
   手順3で確定したrefspecの左辺`<source>`を、そのままbaselineの`--source-ref`へ渡す。
   `--source-ref`へ渡すのはこの左辺だけとし、refspecの右辺`<destination>`、destination ref、remote-tracking refは別の値として扱う。
   baseline作成時に補助スクリプトがsource refをcommitへ再帰的にpeelし、完全長commit SHAを保存する
   - annotated tagとlightweight tagのどちらでもraw tag OIDではなくpeeledしたcommit SHAを保存する
   - pushできるのはcommitへpeelできるrefに限る。peelできないrefではbaseline作成が失敗する
   - GitHubではpush workflowのSHAが更新refのtipであり、GitLabではpipelineがcommit単位ではなくpush単位で起動する
6. 読み込んだ本文書の絶対パスからplugin rootを確定する。
   確定した各`(destination ref, source ref)`について、push前に`uv run --project <plugin-root> --locked --no-default-groups <plugin-root>/agent_toolkit/wait_ci.py`を
   `--write-baseline <手順4で保持した領域の絶対パス>/<呼び出し側が更新refごとに決めた一意なファイル名>.json`付きで実行し、baseline JSONを保存する。
   `wait_ci.py`は直前のコマンドで確定したパスにあり、
   `<plugin-root>/skills/commit/scripts/`には無い

baseline作成、push、監視の順で実行する。
いずれの実行でも`--repo`、`--forge`、`--ref`、`--source-ref`をすべて指定する。
`--repo`にはリポジトリ識別子（`owner/repo`、またはホストを含むURL）を渡す。作業ツリーなどのローカルパスは別の入力として扱う。
引数の詳細は`--help`で確認する。
単一refと複数refのいずれでも、GitHubとGitLabの両方で、選定済みforgeを`--forge <github|gitlab>`へ明示する。
複数refでは組ごとに別のbaselineを作成し、1件が失敗した場合も他のbaselineの作成と監視を続ける。

## pushと監視

1. 標準経路ではremote名とbranch名を明示せず`git push`を単独で実行する。
   明示経路では、成功したdry-runから`--dry-run --porcelain`だけを除いた同一の`<remote> <source>:<destination>`を渡す
2. push成功後、保存した各baselineに対して同スクリプトを`--baseline`付きで実行する
   呼び出し元がCI通過の判定をこのセッションで行わないと明示した場合は、`--baseline`を実行せずに後始末へ進む。
   baselineごとに十分な総待機時間を指定して1回だけ起動する。
   ホストが実行ハンドルのyield・再開を提供する場合は、60秒未満の観測間隔で同一processへ再接続する。
   起動した処理は同じprocessのまま維持する。進捗表示のために短い`--timeout`の別processへ分割する形と、実行中のplugin root更新を契機に置換する形は、いずれも判定対象の実行を取りこぼす
3. 全対象が終了コード0で完了した場合だけCI通過と判定する。
   終了コードの意味は後掲の表に従う。
   出力が空の場合や成功完了マーカーが無い場合は未判定として実測へ切り替える。
   判定対象はbaselineへ保存した完全長SHAに対する実行とし、source refがpush後に進んだ場合も同じSHAで判定する。
   GitHubでpushへ帰属しない自動更新として除外するのは、`event`が`dynamic`かつworkflow名が`Dependabot Updates`である実行に限る。
   同名workflowの手動実行と、他workflowの`dynamic`実行は判定対象へ含める。
   登録猶予の終了後に登録された実行も判定対象に含む。
   登録猶予は、実行が1件も登録されないまま終わる場合を切り分けるための待機であり、
   判定対象を確定する期限ではない
4. CI失敗では、最初の失敗jobを検出した時点でrunまたはpipelineとjobの実識別子、失敗ログ、生成されるartifactを取得し、同一SHAのローカル再現と原因調査を開始する。
   残りのjob監視を継続し、全jobの終端後に失敗集合、ログ、artifactを再照合して修正範囲を確定する。
   長出力の取得と要約は`agents_server`の`start_shell`へ委譲できる。待機と原因分析は自身で行う
5. 証拠取得後に`agent-toolkit:bugfix`を起動し、
   同スキルのCI失敗分析契約で帰属と原因を分類する。
   自セッション帰属または帰属未確定なら、直接的原因の明白さを問わず拡張原因分析経路を適用する
6. CI失敗の修正は、同じbranchへの通常commitとして追加する。push済みcommitへのamend、fixup、rebaseその他の履歴書き換えと、force pushは修正手段の外に置く
7. 診断目的で対象jobを再実行した後も、保存済みの同一baselineに対して`wait_ci.py --baseline`を再起動し、待機はこのスクリプトへ委ねる。自作の待機ループは、baselineが定める判定対象を再現しないため、待機の手段の外に置く。
   許容された再実行後も失敗が残る場合はCI未通過を終端状態として確定し、
   完了報告、採否記録及び計画ファイルの`## 進捗ログ`へ未通過であることと帰属判定を記録する。
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
独立登録した各領域に対し、plan mode外で次を単独実行し、終了コード0を確認する。
終了コード0は対象パスの除去完了を含意するため、この確認だけで除去を判定する。
セッションrootの子領域はセッション終了時の回収へ委ね、個別のcleanupを省く。

```text
atk managed-temp cleanup --path <保持した絶対パス>
```

追加pushでは新しい領域とbaselineを作成する。次の操作、保持理由、正確なパスを記録した
再試行中状態だけは終端まで保持できる。
