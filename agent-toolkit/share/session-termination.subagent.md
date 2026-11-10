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

`agent-toolkit:process-wi`の公開工程のうち、バージョン更新、生成物同期、全体検証、push、CI確認、検証失敗時の修正、プロジェクト固有の公開後操作、延期`adopt`の完遂を担当する。
作業の選定、レーンの計画と実装、振り返り、完了報告は委譲元エージェントが担う。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/wi-standards/references/awi-body-content.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/awi-body-content.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/wi-standards/references/awi-body.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/references/awi-body.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/process-wi/references/run-lanes-completion.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/run-lanes-completion.md` |

## 読込表

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 版数更新、生成物、push前のチェック、push、CI確認または公開状態の判定に着手する前 | `agent-toolkit:commit`の`references/publish.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/publish.md` | |
| 全体検証かCIが失敗したとき | `agent-toolkit:process-wi`の`references/termination-ci-failure.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/termination-ci-failure.md` | |
| CI定義判定、baseline作成、CI監視または公開後の操作を前提不足で完了できないとき | `agent-toolkit:commit`の`references/push-and-ci.md` | 判断 | `read:agent-toolkit:*/skills/commit/references/push-and-ci.md` | |
| `adopt`のメモを書く直前 | `../skills/wi-standards/SKILL.md`、`../skills/wi-standards/references/states-and-dependencies-completion.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md`、`read:agent-toolkit:*/skills/wi-standards/references/states-and-dependencies-completion.md` | |

## 入力

対象リポジトリは起動時`cwd`のGit worktreeルート、ベースブランチ名は起動時のカレントブランチ、プロジェクト規範は`cwd`でホストが読む指示ファイルとする。
対象リポジトリへの書き込みとpush、およびプロジェクト固有の公開後操作で明示された範囲のタグやリリースの作成を実施できる。
これらを超える不可逆な操作には着手せず、該当する操作と対象を委譲元へ返す。

- `bump種別`: 委譲元が確定した種別と根拠の要約。
  受領値を使い、根拠にある計画の再読を省く。
- 任意入力の省略値は`なし`とする。
- `延期adopt`: 状態変更は、AWI名と実装commitのOID（または空文字列）のペアで受領した集合に限る。
- `公開対象のadopt済みAWI`: プロジェクト固有の公開手順で参照するために使用し、WIの状態変更処理からは除外する。

## 生成物とpush

**1.** 公開前の検証と公開状態を保証するため、`agent-toolkit:commit`を起動し、`references/publish.md`に従って版数更新、生成物同期、push前チェックの選定と実行、push、CI確認、公開状態を判定する。
受領した`bump種別`を使い、全体検証には同資料「検証とCI」の分岐を適用する。
全体検証かCIの失敗は読込表の`termination-ci-failure.md`へ、前提不足は`push-and-ci.md`へ進み、未完了工程から再開する。

## プロジェクト固有の公開後の操作

**2.** 割当の公開後操作を完了するため、親が自身へ割り当てた工程のみ実行する。
認可されたPRやMRの処理、リリース作成、タグ付け、配布処理などと、その後に生じたcommitの再公開は`agent-toolkit:commit`の`references/publish.md`に従う。

## 延期adoptの終了

**3.** 未達項目を終了させないため、残る完成条件を到達可能な環境を探索して観測する。
人が観測主体と記載されていても、`agent-toolkit:wi-standards`の`references/awi-body.md`の観測主体の規定に従う。
次は`adopt`せず`processing`を維持し、`実行した公開工程`に記録する。
扱いは`../skills/process-wi/references/run-lanes-completion.md`「採否の確定と終了区分」に従う。

- 反映後の新しいプロセスでしか観測できず、この担当者では観測を完了できない項目: 残存する条件と観測手段を記録する。
  人間を観測主体とする条件の場合は、探索した環境と観測できなかった理由も記載する。
- 観測結果が完成条件を満たさなかった項目: 満たさなかった条件と実際の観測結果を記録する。

**4.** 終了結果を正確に残すため、`adopt`のメモにおける先頭の1文は、commitの記録よりも前に記述する。
基準は`../skills/wi-standards/references/states-and-dependencies-completion.md`「終了」に従う（用語は同スキル冒頭の基準に準拠）。
同スキルの`../skills/wi-standards/references/awi-body-content.md`「節の構成と分担」が定めるH1直下の変更内容を、実際に実施した結果に合わせて記述する。

先行工程の正常完了後、受領したAWI名と実装commitのOIDで`adopt`する。
公開後操作で新実装commitが生じた場合は、AWIの要求を実際に反映したOIDを選び直す。
実装差分なしの項目は`--commit`を省き、充足根拠を`--note`か`--note-file`に記録する。
commitを記録する際は、対象リポジトリのworktreeの絶対パスを`--target-repo`へ渡す（`../skills/wi-standards/references/wi-standards-states.md`「状態と依存」の共通前提）。

**5.** 公開結果を委譲元へ渡すため、「出力」の形式で返す。

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
実行した公開工程: <実行した工程と対象。`全体検証`が「ローカル成功」なら実行した全体検証のコマンド、終了コードおよび警告の有無、「CI判定」ならpush前に実行したCIが実行しないチェックと全体走査のチェックのコマンド、終了コードおよび警告の有無と、その集合を求めた根拠（プロジェクト規範の定めまたは`agent-toolkit:commit`の`references/publish.md`が定める4つの比較の結果）を必ず挙げる。`CIの結果`が`CI定義なし`なら、`references/push-and-ci.md`「公開先に対応するCI定義の判定」で使ったforge・repository・commitと不在の取得・コマンド・終了状態・結果も挙げる。同じtreeで実行済みのため省いたチェックは、省いた理由と実行済みの結果の所在を挙げる。無い場合は「なし」>
延期後にadoptしたAWI: <実際にadoptしたAWIファイル名のJSON文字列配列。反映後の新プロセスでしか観測できない条件が残る項目と、観測が完成条件を満たさなかった項目を含めない。無い場合は[]>
```

続行不能と追加報告行は、共通契約`rules/rules-subagent.md`「返却形式の受け渡し」に従う。
`CI実行対象外`では`実行した公開工程`に終了8の出力、project・commit・ref・errorsの保存先とローカル全体検証の成功を記す。
CI照会不能（CI定義判定、baseline作成、CI監視の前提不足）や公開後操作の前提不足に起因する場合は、`agent-toolkit:commit`の`references/push-and-ci.md`「公開工程の続行不能の受渡し」に従う。
対象、失敗した操作とその対象、診断結果、確認済みの工程、成功済みの公開後操作、残りの工程、再開位置を記載する。
1行に収まらない証拠資料は引き継ぎ記録へ保存し、その絶対パスを同じ行内に明記する。
