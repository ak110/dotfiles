# pushとCI通過確認

実際にpushする直前に本referenceを全文読む。push主体がpush先、更新ref、基準情報、CI監視、
証拠用一時領域のライフサイクルを所有する。通常commit、stage、messageは親スキル、
CI失敗の帰属と原因分析は`agent-toolkit:bugfix`を正本とする。

## push前

1. pushの許可が対象リポジトリと対象branchを含むことを確認する
2. `git fetch`後に上流との差分を双方向で確認する。上流が進んでいる場合は追随後に検証をやり直す
3. `git remote -v`、`git branch --show-current`、有効なpush設定、
   `git push --dry-run --porcelain`の全status lineから、実際のremoteと`<source>:<destination>`をrefごとに確定する。
   拒否または失敗予定のrefがある場合はpushしない
4. 読み込んだ本スキルの絶対パスからplugin rootを確定し、
   `uv run --no-project --script <plugin-root>/scripts/_managed_temp.py create --prefix ci-evidence`を単独で実行する。
   標準出力の絶対パスを保持し、pushごとに別の領域を使う
5. 削除refを除き、更新refごとにsource refを1件確定する。
   baseline作成時に補助スクリプトがsource refをcommitへ再帰的にpeelし、完全長commit SHAを保存する
   - annotated tagとlightweight tagのどちらでもraw tag OIDではなくpeeledしたcommit SHAを保存する
   - commitへpeelできないrefではbaseline作成が失敗するためpushしない
   - GitHubではpush workflowのSHAが更新refのtipであり、GitLabではpipelineがcommit単位ではなくpush単位で起動する
6. 確定した各`(destination ref, source ref)`について、次をpush前に実行してbaseline JSONを保存する

```text
uv run --no-project --script <plugin-root>/scripts/wait_ci.py \
  --write-baseline <baseline-json> \
  --repo <owner/repoまたはURL> \
  --forge <github|gitlab> \
  --ref <destination-ref> \
  --source-ref <source-ref>
```

`--repo`、`--forge`、`--ref`、`--source-ref`は省略しない。
単一refと複数refのいずれでも、GitHubとGitLabの両方で、選定済みforgeを`--forge <github|gitlab>`へ明示する。
複数refでは組ごとに別のbaselineを作成し、1件の失敗を理由に他のbaseline作成・監視を省略しない。

## pushと監視

1. 標準経路ではremote名とbranch名を明示せず`git push`を単独で実行する
2. push成功後、保存した各baselineに対して次を実行する

```text
uv run --no-project --script <plugin-root>/scripts/wait_ci.py \
  --baseline <baseline-json> \
  --repo <owner/repoまたはURL> \
  --forge <github|gitlab> \
  --ref <destination-ref> \
  --source-ref <source-ref>
```

1. 全対象が終了コード0で完了した場合だけCI通過と判定する。
   終了コード1はCI失敗、2はtimeout、3はforge CLIまたは対象判別の失敗、4はrun未登録、130は中断を示す。
   出力が空の場合や成功完了マーカーが無い場合は未判定として実測へ切り替える。
   判定対象はbaselineへ保存した完全長SHAに対する実行であり、source refがpush後に進んでも再解決しない。
   登録猶予の終了後に登録された実行も判定対象に含む。
   登録猶予は、実行が1件も登録されないまま終わる場合を切り分けるための待機であり、
   判定対象を確定する期限ではない
2. CI失敗ではrunまたはpipelineとjobの実識別子、失敗ログ、生成されるartifactを取得する。
   長出力の取得と要約は`agent-toolkit:shell-exec`へ委譲できるが、待機と原因分析は委譲しない
3. 証拠取得後に`agent-toolkit:bugfix`を起動し、
   同スキルの`references/ci-failure-handling.md`で帰属と原因を分類する。
   自セッション帰属または帰属未確定なら、直接的原因の明白さを問わず深掘り経路を適用する

## 後始末

CI成功、バグ対応完了、push失敗、監視不能、run未登録、forge CLI失敗、中断を終端状態とする。
保持した各領域に対し、plan mode外で次を単独実行し、終了コード0とパスの不在を確認する。

```text
uv run --no-project --script <plugin-root>/scripts/_managed_temp.py cleanup --path <保持した絶対パス>
```

追加pushでは新しい領域とbaselineを作成する。次の操作、保持理由、正確なパスを記録した
再試行中状態だけは終端まで保持できる。
