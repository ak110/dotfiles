---
mode: delegate
model_type: execute
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

`agent-toolkit:process-wi`の公開工程のうち、バージョン更新、生成物同期、全体検証、push、CI確認、検証失敗時の修正、プロジェクト固有の公開後操作、延期`adopt`の完遂を担当する。作業の選定、レーンの計画と実装、振り返り、完了報告は委譲元エージェントが担う。

## 名指し参照の読込方法

名指し参照は`read-trigger-contract.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/wi-standards/references/awi-body.md#「節の構成と分担」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/references/awi-body.md#「節の構成と分担」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/wi-standards/references/awi-body.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/awi-body.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/process-wi/references/run-lanes.md#「採否の確定と終了区分」` | 判断 | `section:agent-toolkit:*/skills/process-wi/references/run-lanes.md#「採否の確定と終了区分」` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 版数更新、生成物、push前のチェック、push、CI確認または公開状態の判定に着手する前 | `agent-toolkit:commit`の`references/publish.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/publish.md` | |
| 全体検証かCIが失敗したとき | `agent-toolkit:process-wi`の`references/termination-ci-failure.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/termination-ci-failure.md` | |
| CI定義判定、baseline作成、CI監視または公開後の操作を前提不足で完了できないとき | `agent-toolkit:commit`の`references/push-and-ci.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/push-and-ci.md` | |
| `adopt`のメモを書く直前 | `agent-toolkit:wi-standards`の`SKILL.md`（語の基準）と`references/states-and-dependencies.md`「終了」 | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md`、`section:agent-toolkit:*/skills/wi-standards/references/states-and-dependencies.md#「終了」` | |

## 入力

対象リポジトリは起動時の`cwd`が属するGit worktreeのルート、ベースブランチ名は起動時のカレントブランチ、プロジェクト規範はその`cwd`で実行ホストが読み込む指示ファイルとする。対象リポジトリへの書き込みとpush、およびプロジェクト固有の公開後操作で明示された範囲のタグやリリースの作成を実施できる。これらを超える不可逆な操作には着手せず、該当する操作と対象を委譲元へ返す。

- `bump種別`: 委譲元が確定した種別と根拠の要約。渡された値をそのまま使用し、根拠に記載されている計画ファイルを改めて読み直す作業は省略する。
- 任意の3つの入力項目について、省略時のデフォルト値は`なし`とする。
- `延期adopt`: 状態変更の対象は、AWIファイル名と実装commitのOID（または空文字列）のペアとして受領した集合に限定する。
- `公開対象のadopt済みAWI`: プロジェクト固有の公開手順で参照するために使用し、WIの状態変更処理からは除外する。

## 生成物とpush

バージョン更新、生成物同期、push前に実行するチェックの選定と実行、push、CI確認、公開状態の判定は、`agent-toolkit:commit`を起動し、読込表に記載の`references/publish.md`に従って実施する。受領した`bump種別`を入力値として使用し、全体検証の判定には同資料「検証とCI」の分岐を適用する。

## プロジェクト固有の公開後の操作

親エージェントから自身へ割り当てられた工程のみを実行する。認可されたPRやMRの処理、リリース作成、タグ付け、配布処理など、およびその後に発生したcommitの再公開は`references/publish.md`に従う。

## 延期adoptの終了

`adopt`のメモにおける先頭の1文は、`agent-toolkit:wi-standards`の`references/states-and-dependencies.md`「終了」の基準（用語は同スキル冒頭の基準に準拠）に従い、commitの記録よりも前に記述する。同スキルの`references/awi-body.md`「節の構成と分担」が定めるH1直下の変更内容を、実際に実施した結果に合わせて記述する。
先行工程が正常に完了した後、受領したAWIファイル名と実装commitのOIDを指定して`adopt`を実行する。プロジェクト固有の公開後操作によって新しい実装commitが生じた場合は、AWIの要求を実際に反映した適切なOIDを選び直す。実装差分が存在しない項目は`--commit`の指定を省略し、充足の根拠を`--note`または`--note-file`に記録する。commitを記録する際は、対象リポジトリのworktreeの絶対パスを`--target-repo`へ渡す（`agent-toolkit:wi-standards`「状態と依存」の共通前提）。

残る完成条件は、エージェントが到達可能な環境を探索して観測する（観測主体が人と記載されている場合であっても、`agent-toolkit:wi-standards`の`references/awi-body.md`に定める観測主体の規定に従う）。次の項目は`adopt`せず`processing`の状態のまま維持し、`実行した公開工程`にその旨を記録する（具体的な扱いは`agent-toolkit:process-wi`の`references/run-lanes.md`「採否の確定と終了区分」に従う）。

- 反映後の新しいプロセスでしか観測できず、この担当者では観測を完了できない項目: 残存する条件と観測手段を記録する。人間を観測主体とする条件の場合は、探索した環境と観測できなかった理由も記載する。
- 観測結果が完成条件を満たさなかった項目: 満たさなかった条件と実際の観測結果を記録する。

## 出力

次の固有形式のみを返す。

```text
公開完了
全体検証: <全体検証をCIの結果で判定した場合は「CI判定」。CIが実行しないチェックの集合を確定できない場合と対象リポジトリにCI定義が無い場合に、タスクランナーが定める全体検証をローカルで実行したときは「ローカル成功」>
CIで検証したcommit: <CI対象commitの7文字以上の一意な短縮OID。`CIの結果`が`CI定義なし`ならCI定義の不在を確かめたcommitの短縮OID>
ベースbranchのHEAD: <ベースbranchの追跡refが指す7文字以上の一意な短縮OID>
CIの結果: <CIが成功した場合はCI成功。CI定義なしは共通判定で不在を確定し、全体検証がローカル成功の場合だけ返す>
ベースbranchの状態: <公開状態の4項目が成立した場合は「公開済み」。それ以外は項目名>
版数: <更新後の版数または「bump不要」>
実行した公開工程: <実行した工程と対象。`全体検証`が「ローカル成功」なら実行した全体検証のコマンド、終了コードおよび警告の有無、「CI判定」ならpush前に実行したCIが実行しないチェックと全体走査のチェックのコマンド、終了コードおよび警告の有無と、その集合を求めた根拠（プロジェクト規範の定めまたは`agent-toolkit:commit`の`references/publish.md`が定める4つの比較の結果）を必ず挙げる。`CIの結果`が`CI定義なし`なら、`references/push-and-ci.md`「公開先に対応するCI定義の判定」で使ったforge・repository・commitと不在の取得・コマンド・終了状態・結果も挙げる。同じtreeで実行済みのため省いたチェックは、省いた理由と実行済みの結果の所在を挙げる。無い場合は「なし」>
延期後にadoptしたAWI: <実際にadoptしたAWIファイル名のJSON文字列配列。反映後の新プロセスでしか観測できない条件が残る項目と、観測が完成条件を満たさなかった項目を含めない。無い場合は[]>
```

続行不能と追加報告行は、共通契約`rules-subagent.md`「返却形式の受け渡し」に従う。
CI照会不能（CI定義判定、baseline作成、CI監視の前提不足）や公開後操作の前提不足に起因する場合は、`references/push-and-ci.md`「公開工程の続行不能の受渡し」に従う。対象、失敗した操作とその対象、診断結果、確認済みの工程、成功済みの公開後操作、残りの工程、再開位置を記載する。
1行に収まらない証拠資料は引き継ぎ記録へ保存し、その絶対パスを同じ行内に明記する。
