---
mode: delegate
model_type: execute
result_heads:
- 公開完了
required_inputs:
- bump種別
- 引き継ぎ記録先
optional_inputs:
- プロジェクト固有の公開後の操作
- 延期adopt
- 公開対象のadopt済みAWI
writes_worktree: true
---

# 公開担当タスク

委譲元が選定と報告に集中できるよう、公開担当が公開工程の固定操作を完遂し、検証とCIを通った変更をユーザーへ届ける。
作業`process-wi`の公開工程のうち、版数の更新、生成物の同期、全体検証、push、CIの確認、検証の失敗時の修正、プロジェクト固有の公開後の操作、延期`adopt`の完遂を担当する。
作業の選定、レーンの計画と実装、振り返り、完了報告は委譲元が担う。
公開工程の全体の流れは`agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`にある。

## 入力

対象リポジトリは起動時の`cwd`のGit worktreeのルート、ベースブランチの名前は起動時のカレントブランチ、プロジェクト規範は`cwd`でホストが読む指示ファイルとする。
push先はカレントブランチの上流追跡ブランチとし、カレントブランチと名前が違う場合もブランチを切り替えずに上流追跡ブランチへpushする。
対象リポジトリへの書き込みとpushと、プロジェクト固有の公開後の操作で明示された範囲のタグやリリースの作成を行える。
これらを超える取り消しの難しい操作には着手せず、該当の操作と対象を委譲元へ返す。

- `bump種別`: 委譲元が確定した種別と根拠の要約。
  受け取った値を使い、根拠にある計画の再読を省く。
- 任意入力の省略値は`なし`とする。
- `延期adopt`: 状態の変更は、AWI名と実装commitのOID（か空文字列）の組で受け取った集合に限る。
- `公開対象のadopt済みAWI`: プロジェクト固有の公開の手順で参照するために使い、WIの状態の変更からは除く。

## 生成物とpush

1. 着手前に`agent-toolkit:atk-mode/playbooks/commit.md`と`agent-toolkit:atk-mode/playbooks/publish.md`を全文読み、それに従って公開する。
   版数の更新、生成物の同期、push前のチェックの選定と実行、push、CIの確認をして、公開状態を判定する。
   受け取った`bump種別`を使い、全体検証には同書「検証とCI」の分岐を適用する。
2. 全体検証かCIの失敗は`agent-toolkit:atk-mode/playbooks/process-wi-ci-fix.md`「検証またはCIの失敗」へ進む。
   CI定義の判定、baselineの作成、CIの監視、公開後の操作を前提不足で終えられない時は`agent-toolkit:atk-mode/playbooks/publish.md`へ進み、未完了の工程から再開する。

## プロジェクト固有の公開後の操作

親が自分へ割り当てた工程だけを実行する。
認可されたPRやMRの処理、リリースの作成、タグ付け、配布の処理と、その後に生じたcommitの再公開は`agent-toolkit:atk-mode/playbooks/publish.md`に従う。

## 延期adoptの終了

1. 残る完成条件を、届く環境を探して観測する。
   人が観測主体と書かれていても、`agent-toolkit:atk-mode/references/awi-body.md`の観測主体の規定に従う。
2. 次の項目は`adopt`せずに`processing`を保ち、`実行した公開工程`に記録する（扱いは`agent-toolkit:atk-mode/playbooks/process-wi-lanes.md`「採否の確定と終了区分」）。
   - 反映後の新しいプロセスでしか観測できず、この担当では観測を終えられない項目: 残る条件と観測の手段を記録し、人間を観測主体とする条件では探した環境と観測できなかった理由も書く。
   - 観測結果が完成条件を満たさなかった項目: 満たさなかった条件と実際の観測結果を記録する。
3. `adopt`のメモの先頭の1文は、commitの記録より前に書く。
   基準は`agent-toolkit:atk-mode/references/states-and-dependencies.md`「終了」に従う（用語は`agent-toolkit:atk-mode/playbooks/wi-drafting-inputs.md`「ユーザーが読む部分の合格基準」）。
   `agent-toolkit:atk-mode/references/awi-body.md`「節の構成と分担」のH1直下の変更内容を、実際に行った結果に合わせて書く。
4. 先行の工程が正常に終わったら、受け取ったAWI名と実装commitのOIDで`adopt`する。
   公開後の操作で新しい実装commitが生じた場合は、AWIの要求を実際に反映したOIDを選び直す。
   実装差分の無い項目は`--commit`を省き、充足の根拠を`--note`か`--note-file`に書く。
   commitを記録する時は、対象リポジトリのworktreeの絶対パスを`--target-repo`へ渡す（同書「状態と依存」の共通の前提）。
5. 公開結果を委譲元へ渡すため、「出力」の形式で返す。

## 出力

次の固有形式のみを返す。

```text
公開完了
全体検証: <全体検証をCIの結果で判定した場合は「CI判定」。CIが実行しない集合を確定できない場合、CI定義なし、CI実行対象外で、タスクランナーの全体検証をローカルで実行し成功した場合は「ローカル成功」>
CIで検証したcommit: <CI対象commitの7文字以上の一意な短縮OID。CI定義なし・CI実行対象外なら不在・非生成を確かめたcommitの短縮OID>
ベースbranchのHEAD: <ベースbranchの追跡refが指す7文字以上の一意な短縮OID>
CIの結果: <CI成功|CI定義なし|CI実行対象外。後二者は共通判定の不在・非生成の証拠と全体検証のローカル成功を確認した場合だけ返す>
ベースbranchの状態: <公開状態の4項目が成立した場合は「公開済み」。それ以外は項目名>
版数: <更新後の版数または「bump不要」>
実行した公開工程: <実行した工程と対象。`全体検証`が「ローカル成功」なら実行した全体検証のコマンド、終了コードおよび警告の有無、「CI判定」ならpush前に実行したCIが実行しないチェックと全体走査のチェックのコマンド、終了コードおよび警告の有無と、その集合を求めた根拠（プロジェクト規範の定めまたは`agent-toolkit:atk-mode/playbooks/publish.md`が定める4つの比較の結果）を必ず挙げる。`CIの結果`が`CI定義なし`なら、`agent-toolkit:atk-mode/references/ci.md`「公開先に対応するCI定義の判定」で使ったforge・repository・commitと不在の取得・コマンド・終了状態・結果も挙げる。同じtreeで実行済みのため省いたチェックは、省いた理由と実行済みの結果の所在を挙げる。無い場合は「なし」>
延期後にadoptしたAWI: <実際にadoptしたAWIファイル名のJSON文字列配列。反映後の新プロセスでしか観測できない条件が残る項目と、観測が完成条件を満たさなかった項目を含めない。無い場合は[]>
```

続行不能と追加報告行は、共通契約`agent-toolkit:atk-mode/playbooks/subagent.md`「返却形式の受け渡し」に従う。
`CI実行対象外`では`実行した公開工程`に終了8の出力、project・commit・ref・errorsの保存先とローカル全体検証の成功を記す。
CI照会不能（CI定義判定、baseline作成、CI監視の前提不足）や公開後操作の前提不足に起因する場合は、`agent-toolkit:atk-mode/playbooks/publish.md`「公開工程の続行不能の受渡し」に従う。
対象、失敗した操作とその対象、診断結果、確認済みの工程、成功済みの公開後操作、残りの工程、再開位置を記載する。
1行に収まらない証拠資料は引き継ぎ記録へ保存し、その絶対パスを同じ行内に明記する。
