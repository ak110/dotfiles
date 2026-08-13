# pushとCI通過確認

実際にpushする直前に本referenceを全文読む。push主体がpush先、更新ref、基準情報、CI監視、
証拠用一時領域のライフサイクルを所有する。通常commit、stage、messageは親スキル、
CI失敗の帰属と原因分析は`agent-toolkit:bugfix`を正本とする。

## push前

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

   明示経路ではremote、source、完全なdestination refを省略しない。
   いずれの経路でも拒否または失敗予定のrefがある場合はpushしない
4. 読み込んだ本スキルの絶対パスからplugin rootを確定し、
   `uv run --no-project --script <plugin-root>/scripts/_managed_temp.py create --prefix ci-evidence`を単独で実行する。
   標準出力の絶対パスを保持し、pushごとに別の領域を使う
5. 削除refを除き、更新refごとにsource refを1件確定する。
   手順3で確定したrefspecの左辺`<source>`を、そのままbaselineの`--source-ref`へ渡す。
   refspecの右辺`<destination>`、destination ref、remote-tracking refを代用しない。
   baseline作成時に補助スクリプトがsource refをcommitへ再帰的にpeelし、完全長commit SHAを保存する
   - annotated tagとlightweight tagのどちらでもraw tag OIDではなくpeeledしたcommit SHAを保存する
   - commitへpeelできないrefではbaseline作成が失敗するためpushしない
   - GitHubではpush workflowのSHAが更新refのtipであり、GitLabではpipelineがcommit単位ではなくpush単位で起動する
6. 確定した各`(destination ref, source ref)`について、push前に
   `uv run --no-project --script <plugin-root>/scripts/wait_ci.py`を`--write-baseline`付きで実行し、
   baseline JSONを保存する

baseline作成、push、監視の順で実行する。
いずれの実行でも`--repo`、`--forge`、`--ref`、`--source-ref`を省略しない。
引数の詳細は`--help`で確認する。
単一refと複数refのいずれでも、GitHubとGitLabの両方で、選定済みforgeを`--forge <github|gitlab>`へ明示する。
複数refでは組ごとに別のbaselineを作成し、1件の失敗を理由に他のbaseline作成・監視を省略しない。

## pushと監視

1. 標準経路ではremote名とbranch名を明示せず`git push`を単独で実行する。
   明示経路では、成功したdry-runから`--dry-run --porcelain`だけを除いた同一の`<remote> <source>:<destination>`を渡す
2. push成功後、保存した各baselineに対して同スクリプトを`--baseline`付きで実行する
   baselineごとに十分な総待機時間を指定して1回だけ起動する。
   ホストが実行handleのyield・再開を提供する場合は、60秒未満の観測間隔で同一processへ再接続する。
   進捗表示のために短い`--timeout`を指定した別processへ分割せず、実行中のplugin root更新を理由に置換しない
3. 全対象が終了コード0で完了した場合だけCI通過と判定する。
   終了コードの意味は後掲の表に従う。
   出力が空の場合や成功完了マーカーが無い場合は未判定として実測へ切り替える。
   判定対象はbaselineへ保存した完全長SHAに対する実行であり、source refがpush後に進んでも再解決しない。
   登録猶予の終了後に登録された実行も判定対象に含む。
   登録猶予は、実行が1件も登録されないまま終わる場合を切り分けるための待機であり、
   判定対象を確定する期限ではない
4. CI失敗では、最初の失敗jobを検出した時点でrunまたはpipelineとjobの実識別子、失敗ログ、生成されるartifactを取得し、同一SHAのローカル再現と原因調査を開始する。
   残りのjob監視を継続し、全jobの終端後に失敗集合、ログ、artifactを再照合して修正範囲を確定する。
   長出力の取得と要約は`agent-toolkit:shell-exec`へ委譲できるが、待機と原因分析は委譲しない
5. 証拠取得後に`agent-toolkit:bugfix`を起動し、
   同スキルの`references/ci-failure-handling.md`で帰属と原因を分類する。
   自セッション帰属または帰属未確定なら、直接的原因の明白さを問わず深掘り経路を適用する
6. 診断目的で対象jobを再実行した後も、保存済みの同一baselineに対して`wait_ci.py --baseline`を再起動する。
   自作の待機ループへ置き換えない。許容された再実行後も失敗が残る場合はCI未通過を終端状態として確定し、
   完了報告、採否記録及び計画の進捗ログへ未通過であることと帰属判定を記録する

`wait_ci.py`の終了コードは次のとおり。

| 終了コード | 意味 |
| --- | --- |
| 0 | CI通過 |
| 1 | CI失敗 |
| 2 | timeout |
| 3 | forge CLIまたは対象判別の失敗 |
| 4 | run未登録 |
| 130 | 中断 |

## 後始末

CI成功、バグ対応完了、push失敗、監視不能、run未登録、forge CLI失敗、中断を終端状態とする。
保持した各領域に対し、plan mode外で次を単独実行し、終了コード0とパスの不在を確認する。

```text
uv run --no-project --script <plugin-root>/scripts/_managed_temp.py cleanup --path <保持した絶対パス>
```

追加pushでは新しい領域とbaselineを作成する。次の操作、保持理由、正確なパスを記録した
再試行中状態だけは終端まで保持できる。
