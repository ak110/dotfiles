# 終端担当の起動と受領

```text
起動対象: session-termination.subagent.md
```

`agent-toolkit:process-wi`の公開工程で、メインが終端担当を起動し、入力を渡し、返却値を検収するときに本書を適用する。
委譲先の作業手順は`${CLAUDE_PLUGIN_ROOT}/share/session-termination.subagent.md`が定める。

## 名指し参照の読込方法

名指し参照は`read-trigger-contract.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `session-termination.subagent.md` | 判断 | `read:agent-toolkit:*/share/session-termination.subagent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/process-wi/references/finish-session.md#「公開工程の続行不能の回復と再開」` | 判断 | `section:agent-toolkit:*/skills/process-wi/references/finish-session.md#「公開工程の続行不能の回復と再開」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/base-contract.md#「引き継ぎ記録先」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/base-contract.md#「引き継ぎ記録先」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/commit/references/push-and-ci.md#「公開先に対応するCI定義の判定」` | 判断 | `section:agent-toolkit:*/skills/commit/references/push-and-ci.md#「公開先に対応するCI定義の判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/commit/references/push-and-ci.md#「公開状態の4項目」` | 判断 | `section:agent-toolkit:*/skills/commit/references/push-and-ci.md#「公開状態の4項目」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/completion-report/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/completion-report/SKILL.md` |

## 起動前の前提

全レーンの終端を確かめ、対象リポジトリの主作業ツリーへ書き込む主体が自身だけであることを確定してから起動する。
起動時の`cwd`は対象リポジトリの主作業ツリーとし、現在branchを公開対象のベースbranchにしておく。

## 起動方法

`agents_server`の`start`を次の引数で呼び、終端担当を1件起動する。

| 引数 | 値 |
| --- | --- |
| `cwd` | 対象リポジトリの絶対パス |
| `subagent_md_path` | `session-termination` |
| `extra_params` | `## 渡す入力`の名前付き入力 |

同じセッションで前回の公開後に新しいcommitを統合した場合は、追加差分に属する項目を入力として再び起動する。
新しいcommitが無くCI未判定または未完了の公開後の操作が残る場合は、`agent-toolkit:process-wi`の`references/finish-session.md`「公開工程の続行不能の回復と再開」に従う。
同じ終端担当sessionを保持していれば回答を渡し、保持していなければ同じ公開先・commitの未完了工程を入力として新たに起動する。
再開では`bump種別`を`bump不要`とし、既存の`引き継ぎ記録先`を渡す。記録から公開対象のcommit、確認済み工程、CI結果、未完了の公開後の操作と再開位置を引き継ぎ、成功済みの版数更新やpushを繰り返さない。
CIの検収と公開後の操作が完了し、新しいcommitが無い場合は、再び起動しない。

## 渡す入力

- `bump種別`: 今回の未公開差分全体に対して確定した最上位の種別と選定根拠の要約（AWI起草時の区分より上位の変更があれば上位区分）。該当する記録が無い場合は`bump不要`。計画ファイルのパスは渡す入力から除く
- `プロジェクト固有の公開後の操作`: ある場合だけ、対象と依存順。終端担当が待機する長時間工程と、親が別のエージェントへ渡して並行開始する工程（情報として示し、終端担当の実行対象から外す）を区別する
- `延期adopt`: ある場合だけ、対象AWIファイル名、先行する終端工程、`adoptを延期したAWIとcommit`で検収した実装commitのOID（実装差分が無い項目は空文字列）。反映後の新プロセスでしか観測できない条件には、残る完成条件と観測手段も含める
- `公開対象のadopt済みAWI`: この公開に含むcommitが終端したAWIファイル名の集合。ある場合だけ渡し、WI状態変更の入力からは除く
- `引き継ぎ記録先`: 省略と継続の扱いは`agent-toolkit:delegation`の`references/base-contract.md`「引き継ぎ記録先」に従う

任意の3入力の省略時の値は`なし`とし、その場合は行を省く。

## 受領と検収

最初に`続行できない理由:`の有無を判別する。
CI照会不能（CI定義判定・baseline作成・CI監視の前提不足）または確認済みpush後の公開後の操作の前提不足の返却からは、対象forge・repository・commit、失敗操作と対象、診断、確認済み工程、成功済みの公開後の操作、残工程、引き継ぎ記録を受け取る。
確認済みpushと公開状態の4項目を現在の対象ref・Git状態で検収し、CI未判定は別に保持する。
正常完了形式への差し戻しは行わず、`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/finish-session.md`「公開工程の続行不能の回復と再開」へ渡す。
必要な対象や記録が不足する場合は、その不足だけを同じ担当へ求める。
push前の工程（版数更新、生成物、push前のチェック、push）の失敗は、失敗した工程の既存手順で回復し、確認済み工程を保持する。

`終端完了`に続く8行と現在のGit状態で次を確かめる（成果物と実装差分の再読解は要しない）。一致しない場合は同じ終端担当へ差し戻す。

- ベースbranchとそのリモート追跡refを1件ずつ`git -C <対象リポジトリ> rev-parse --verify --short=7 '<対象ref>^{commit}'`へ渡して得た値が、いずれも`ベースbranchのHEAD`と一致する
- `全体検証`が`CI判定`か`ローカル成功`である。`ローカル成功`なら`実行した終端工程`が全体検証のコマンド、終了コードと警告の有無を挙げる。`CI判定`ならpush前に実行したチェックのコマンド、終了コード0と警告の扱い、およびその集合を求めた根拠を挙げる。省いたチェックには省いた理由と実行済みの結果の所在がある
- `CIの結果`が次のいずれかである。それ以外は差し戻す
  - `CI成功`: 対象リポジトリのCI照会手段が`CIで検証したcommit`について同じ結論を返す
  - `CI定義なし`: `全体検証`が`ローカル成功`であり、`agent-toolkit:commit`の`references/push-and-ci.md`「公開先に対応するCI定義の判定」で同じ公開先と`ベースbranchのHEAD`の不在を検収できる。`実行した終端工程`は対象forge・repository・commitと不在を確かめた取得・コマンド・終了状態・結果を挙げる
- `CIで検証したcommit`と`ベースbranchのHEAD`が異なり、後者自体のCI成功を前項で検収していない場合は、間のcommit集合が`実行した終端工程`の挙げる生成commit（操作直前に解決したOID）と過不足なく一致することを確かめる。集合は`git -C <対象リポジトリ> rev-list <CIで検証したcommit>..<ベースbranchのHEAD>`で取り、`ベースbranchのHEAD`がマージcommitなら`--not <ベースbranchのHEAD>^1`を加えてベース側系列を除く（マージcommit本体は含める）。この確認はHEADのCI成功の判定を代替しない
- `ベースbranchの状態`が`公開済み`であることを、`agent-toolkit:commit`の`references/push-and-ci.md`「公開状態の4項目」を現在のGit状態から再取得して判定する。差し戻しても`公開済み`にならない場合は`agent-toolkit/skills/process-wi/references/finish-session.md`「公開状態の検収と再起動」のUWI登録へ送る
- `版数`が`bump不要`でなければ、対象リポジトリの版数規範が定める定義ファイルの版数と一致する
- `実行した終端工程`が、終端担当の実行対象としたプロジェクト固有の公開後の操作を過不足なく挙げる。並行開始した工程は担当エージェントの返却から別に検収する
- `延期後にadoptしたAWI`をJSON parserで文字列配列として検証し（不正JSON、配列以外、文字列以外の要素は返却契約の不成立）、起動時に渡した延期`adopt`対象の部分集合であることを確かめる。
  差集合は反映後の新プロセスでしか観測できない条件を持つ項目（`実行した終端工程`が残る条件と観測手段を挙げる）と、観測が完成条件を満たさなかった項目（満たさなかった条件と観測結果を挙げる）に限る。扱いは`${CLAUDE_PLUGIN_ROOT}/skills/process-wi/references/run-lanes.md`「採否の確定と終端区分」が定める
- 延期`adopt`対象がある場合は、全対象を名指しした`atk wi show <ファイル名>... --summary-only --target-repo=<対象リポジトリの絶対パス> --skip-pull`を1回実行する。
  `### <ファイル名> [<状態>]`の見出しが`延期後にadoptしたAWI`では`adopted`、差集合では`processing`であることを確かめる

全ての確認の後、延期対象から`延期後にadoptしたAWI`を除いた集合を`agent-toolkit/skills/process-wi/references/finish-session.md`「延期adoptと反映後の観測」へ、検収した公開状態の4項目を同書「公開状態の検収と再起動」へ渡す。間に主作業ツリー・対象refの変更や外部更新を観測した場合は、同節に従って現在状態を取得し直す。
未終端集合の処置の後、同書「完了報告と終了」に従って`agent-toolkit:completion-report`による報告と`atk agents-exit-session`を実施する。
all-reposのリポジトリ担当は同節に従い、`atk agents-exit-session`の代わりに常駐プログラムへの最終返却でターンを終える。
