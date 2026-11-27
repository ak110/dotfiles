# 公開担当の起動と受領

```text
起動対象: session-termination.subagent.md
```

選定とレーンを担う主体が公開の固定操作を公開担当へ任せても自分で行っても、検証とCIを通った変更だけをユーザーへ公開できるようにするための文書である。
作業`process-wi`の公開工程で、統括が直接実行する場合と、公開担当へ委譲して検収する場合に適用する。
公開工程の流れは`agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`に、委譲先の手順は`agent-toolkit:atk-mode/roles/session-termination.subagent.md`にある。

## 起動前の前提

1. 公開する変更の統合とバージョン・生成物・検証の完了を確かめる。
   公開担当は主作業ツリーを書き換えず、同じ対象の公開だけを1件ずつ進める。
2. 公開の短絡・直接実行・委譲のどれでも、`agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`「公開対象が無い場合の短絡」の選定全量か、同書「独立した公開単位」の指定群と先行の閉包を確かめ、終了コード0を確かめてから公開する。
   - 公開中の並行工程は`agent-toolkit:atk-mode/playbooks/publish.md`「push前」に従う。
   - 単位を分けた場合は、公開対象のcommit・WI・入力SHAと未完了の群の再開先を既存の引き継ぎ記録で受け渡し、単位の公開後に残りへ戻る。
   - 未統合があれば元のレーンで残工程を続けるか、正当な延期の理由・解除条件・再開先を保存して公開の可否を判定する。
   - 照会結果と選定の原本の絶対パスを既存の引き継ぎ記録へ残し、全体の完了は起動済みのレーンの終了とは別に判定する。
3. 公開する統合結果を持つ主作業ツリー（`agent-toolkit:atk-mode/playbooks/process-wi.md`「用語」。all-reposでは統括が渡す対象要素の`worktree`）を公開工程の`cwd`とする。
   ブランチは主作業ツリーが今checkoutしているものをそのまま使い、公開先のブランチと名前が違う場合も切り替えない。
4. 本書「渡す入力」へ進む。

## 渡す入力

<!-- input-contract:session-termination:start -->
<!-- 生成元: skills/atk-mode/roles/session-termination.subagent.mdのrequired_inputs・optional_inputs。手動編集しない。 -->
入力の定義はこの起動契約とagents_serverの`start`の検査が所有する。
役割固有の入力（`extra_params`）はこの契約の入力一覧で完結する。
起動の準備で`<役割名>.subagent.md`の本文は読まず、入力はこの一覧で決め、形式の確認は`start`に任せる。
通常起動の前に`agent-toolkit:atk-mode/references/work-contract.md`に従う全体作業契約を用意する。
`agents_server.start`に役割文書の`subagent_md_path`と、以下の役割固有の入力をまとめた`extra_params`を渡す。
`mode`は省略する（明示する場合は`mode: task`）。
全体作業契約の絶対パスは`extra_params`の外の`whole_contract_path`に渡す。

- 必須入力名: `bump種別`、`引き継ぎ記録先`
- 任意入力名: `プロジェクト固有の公開後の操作`、`延期adopt`、`公開対象のadopt済みAWI`

<!-- input-contract:session-termination:end -->

既決の公開判断を引き継ぐため、必須入力と該当する任意入力を確定する。

- `bump種別`: `agent-toolkit:atk-mode/playbooks/publish.md`「バージョンと生成物」で確定した種別と根拠の要約。
  記録が無ければ`bump不要`とし、計画のパスは省く。
- `プロジェクト固有の公開後の操作`: ある場合に、対象と依存の順を指定する。
  公開担当が完了を待つ長い工程と、親が別の担当へ任せて並行して始める工程を区別し、後者は情報の共有として公開担当の実行対象から除く。
  公開後にリリース周期を判定する規定があれば、その定義元、次の公開単位とCIの見込み、今回の差分にリリース先の不具合修正が含まれるかを渡す。
  CI成功時の判定と、必要ならリリースの作成・マージ・検収を公開担当自身の操作として指定する。
  直接実行でも同じ条件で判定する。
- `延期adopt`: ある場合に、対象のAWIファイル名、先行する公開工程、`adoptを延期したAWIとcommit`で検収した実装commitのOID（実装差分が無い項目は空文字列）を指定する。
  反映後の新しいプロセスでしか観測できない条件を含む場合は、残る完成条件と観測手段も渡す。
- `公開対象のadopt済みAWI`: 今回の公開に含むcommitで終了したAWIファイル名の集合。
  ある場合に渡し、WIの状態変更の入力からは除く。
- `引き継ぎ記録先`: 省略時と継続時の扱いは`agent-toolkit:atk-mode/references/delegation-prompt.md`「引き継ぎ記録先」に従う。

任意の3入力の省略値は`なし`とし、該当の行を省く。
確定したら本書「直接実行」へ進む。

## 直接実行

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 公開工程を直接実行する前 | `agent-toolkit:atk-mode/roles/session-termination.subagent.md` |

1. 常駐では統括が対象ごとに公開担当を起動し、リポジトリ担当は実装と統合を続ける。
   手動の単回実行は、計画と既決のbump判断を保ってメインが直接実行することを通常とする。
   公開担当への委譲は`agent-toolkit:atk-mode/playbooks/delegation.md`「要否判定」に従う（既決のbumpの判断を引き継ぐ）。
2. 直接実行では、`agent-toolkit:atk-mode/roles/session-termination.subagent.md`「入力」から「延期adoptの終了」までの手順と読み込みの条件を自分へ適用する。
   同書の公開担当は自分、委譲元への返却は自分の既存の進捗記録への保存として扱い、「出力」の公開結果の項目を残す。
   役割の起動、返却待ち、自分へのメッセージ送信と、本書「受領と検収」の委譲した返却との照合は省き、公開状態と未終了の項目など該当する手順だけを適用する。
   実行する操作は現在の依頼と認可の範囲に限る。
   全体検証かCIの失敗は`agent-toolkit:atk-mode/playbooks/process-wi-ci-fix.md`「検証またはCIの失敗」へ進み、CI修正担当の起動中は自分の書き込みを止める。
   実行レビュー、公開後の操作、延期`adopt`、未完了の工程の保存と再開、公開状態の検収は保つ。

## 起動方法

1. 委譲を選んだ場合は、`agents_server`の`start`を次の引数で呼んで公開担当を1件起動し、終了後に本書「受領と検収」へ進む。

   | 引数 | 値 |
   | --- | --- |
   | `cwd` | 「起動前の前提」手順3で確定した主作業ツリーの絶対パス |
   | `subagent_md_path` | `session-termination` |
   | `extra_params` | `## 渡す入力`の名前付き入力 |

   起動の結果に返ったsessionの識別子を既存の進捗記録へ残す。

2. 同じセッションで前回の公開後に新しいcommitを統合した場合は、追加の差分の項目を入力に起動し直す。
3. 新しいcommitが無く、CIが未判定か公開後の操作が未完了なら、`agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`「公開工程の続行不能の回復と再開」に従う。
   公開担当のsessionがあれば回答を渡し、無ければ同じ公開先とcommitの未完了の工程を入力に新しく起動する。
   再開時は`bump種別`を`bump不要`にし、既存の`引き継ぎ記録先`を渡す。
4. CIの検収と公開後の操作が終わり、新しいcommitが無ければ起動し直さずに本書「受領と検収」へ進む。

<!-- return-contract:session-termination:start -->
<!-- 生成元: skills/atk-mode/roles/session-termination.subagent.md「出力」。手動編集しない。 -->
## 公開担当の返却契約

CI修正が必要なら、pushしたcommit・失敗job・診断・CI記録・残る監視と延期adoptを保存し、共通形式の`状態: incomplete`で統括へ返す。
失敗したcommitの公開を完了扱いにせず、通常のレーンで修正を統合して次のpushへ入れ、同じ担当でCIの確認を再開する。
成功時は次の固有形式のみを返す。

```text
公開完了
全体検証: <全体検証をCIの結果で判定した場合は「CI判定」。CIが実行しない集合を確定できない場合、CI定義なし、CI実行対象外で、タスクランナーの全体検証をローカルで実行し成功した場合は「ローカル成功」>
CIで検証したcommit: <CI対象commitの7文字以上の一意な短縮OID。CI定義なし・CI実行対象外なら不在・非生成を確かめたcommitの短縮OID>
ベースbranchのHEAD: <今回pushした公開単位の完全OID。後続の統合で進んだHEADとは区別する>
CIの結果: <CI成功|CI定義なし|CI実行対象外。後二者は共通判定の不在・非生成の証拠と全体検証のローカル成功を確認した場合だけ返す>
ベースbranchの状態: <公開状態の4項目が成立した場合は「公開済み」。それ以外は項目名>
版数: <更新後のバージョンまたは「bump不要」>
実行した公開工程: <実行した工程と対象。`全体検証`が「ローカル成功」なら実行した全体検証のコマンド、終了コードおよび警告の有無、「CI判定」ならpush前に実行したCIが実行しないチェックと全体走査のチェックのコマンド、終了コードおよび警告の有無と、その集合を求めた根拠（プロジェクト規範の定めまたは`agent-toolkit:atk-mode/playbooks/publish.md`が定める4つの比較の結果）を必ず挙げる。`CIの結果`が`CI定義なし`なら、`agent-toolkit:atk-mode/references/ci.md`「公開先に対応するCI定義の判定」で使ったforge・repository・commitと不在の取得・コマンド・終了状態・結果も挙げる。同じtreeで実行済みのため省いたチェックは、省いた理由と実行済みの結果の所在を挙げる。無い場合は「なし」>
延期後にadoptしたAWI: <実際にadoptしたAWIファイル名のJSON文字列配列。反映後の新プロセスでしか観測できない条件が残る項目と、観測が完成条件を満たさなかった項目を含めない。無い場合は[]>
```

`agent-toolkit:atk-mode/playbooks/subagent.md`「返却形式の受け渡し」に従う。
`CI実行対象外`では`実行した公開工程`に終了8の出力、project・commit・ref・errorsの保存先とローカル全体検証の成功を記す。
CI照会不能（CI定義判定、baseline作成、CI監視の前提不足）や公開後操作の前提不足に起因する場合は、`agent-toolkit:atk-mode/playbooks/publish.md`「公開工程の続行不能の受渡し」に従う。
対象、失敗した操作とその対象、診断結果、確認済みの工程、成功済みの公開後操作、残りの工程、再開位置を記載する。
1行に収まらない証拠資料は引き継ぎ記録へ保存し、その絶対パスを同じ行内に明記する。
<!-- return-contract:session-termination:end -->

## 受領と検収

1. CI修正待ちの`状態: incomplete`なら、失敗した入力OIDとCI記録を保ち、通常のレーンで修正を統合する。
   旧OIDの公開は進行中から外し、修正を含むベースブランチの次のpushへ進む。
   それ以外では完了済みの工程を保つため、最初に`続行できない理由:`の行があるかを確かめる。
   - CIの照会不能（CI定義の判定、baselineの作成、CIの監視の前提不足）か、確認済みのpush後の公開後の操作の前提不足による返却では、対象のforge・repository・commit、失敗した操作と対象、診断、確認済みの工程、成功済みの公開後の操作、残る工程、引き継ぎ記録を受け取る。
     pushと公開状態は`agent-toolkit:atk-mode/playbooks/publish.md`「公開状態の4項目」で検収し、CIの未判定は別に保つ。
     正常完了の形式へ差し戻さず、`agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`「公開工程の続行不能の回復と再開」へ渡し、対象や記録が足りなければ足りない分だけを同じ担当へ求める。
   - push前の工程で失敗した場合は、失敗した工程の既存の手順で回復する。
2. 返却された`公開完了`に続く8行を現在のGit状態と比べ、次を確かめる（成果物と実装差分の再読は省く）。
   `agent-toolkit:atk-mode/playbooks/delegation-receiving.md`「終了と報告内容の確認」に従う。
   `<対象リポジトリ>`には起動時の`cwd`と同じ主作業ツリーを指定する。
   返却の`ベースbranchのHEAD`が、実際にpushしたcommitと一致することを公開記録から確かめる。
   - 公開先refは`git ls-remote`で照会する。
   - `全体検証`が`CI判定`か`ローカル成功`である。
     `ローカル成功`では`実行した公開工程`に全体検証のコマンド、終了コード、警告の有無があり、`CI判定`ではpush前に実行したチェックのコマンド、終了コード0、警告の扱い、その実行集合を導いた根拠がある。
     除いたチェックがあれば、除いた理由と実行済みの結果の所在が示されている。
   - `CIの結果`が次のどれかである（当たらなければ差し戻す）。
     - `CI成功`: 対象リポジトリのCIの照会の手段で、`CIで検証したcommit`の成功の結論が得られる。
     - `CI実行対象外`: 全体検証がローカル成功で、終了8の出力のproject・commit・ref・errorsから同じ公開先と`ベースbranchのHEAD`での非生成を検収でき、`実行した公開工程`に証拠の保存先がある。
     - `CI定義なし`: `全体検証`が`ローカル成功`で、`agent-toolkit:atk-mode/references/ci.md`「公開先に対応するCI定義の判定」で同じ公開先と`ベースbranchのHEAD`のCI定義の不在を検収できる。
       `実行した公開工程`に、対象のforge・repository・commitと、不在を確かめた取得内容・コマンド・終了状態・結果がある。
   - `CIで検証したcommit`と`ベースbranchのHEAD`が異なり、後者自身のCIの成功を前項で検収していない場合は、`git -C <対象リポジトリ> rev-list <CIで検証したcommit>..<ベースbranchのHEAD>`で得たcommitの集合を求める。
     その集合が、`実行した公開工程`に挙げた生成commit（操作の直前に解決したOID）と過不足なく一致する。
     `ベースbranchのHEAD`がマージcommitなら`--not <ベースbranchのHEAD>^1`を加えてベース側の履歴を除く（マージcommit自体は含める）。
     HEAD自体のCIの成功は、この確認とは別に判定する。
   - `ベースbranchの状態`は`agent-toolkit:atk-mode/playbooks/publish.md`「公開状態の4項目」に従って判定する。
     差し戻しても`公開済み`にならなければ、`agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`「公開状態の検収と再起動」に従ってUWIの登録へ回す。
   - `版数`が`bump不要`でなければ、pushしたcommitの定義ファイルのバージョンと一致する。
   - `実行した公開工程`に、公開担当自身の実行対象と定めたプロジェクト固有の公開後の操作が過不足なく並んでいる（並行して始めた工程は担当の返却から個別に検収する）。
     リリース判定を割り当てた場合は、対象OID・CI・最古の未反映commitと経過時間・期限が判定結果と一致することを確かめる。
     必要なリリースの作成・マージ・検収の結果、見送りや続行不能なら次の動作・判定時機・担当（阻害があればその内容）を`実行した公開工程`か同欄が示す引き継ぎ記録から受け取る。
     未完了のリリース判定の所有は、[公開状態の検収と再起動](../playbooks/process-wi-publishing.md#公開状態の検収と再起動)に従う。
   - `延期後にadoptしたAWI`をJSONパーサーで文字列配列として検証し（構文エラー、配列以外の型、文字列以外の要素は返却契約の違反）、起動時に渡した延期`adopt`の対象の部分集合であることを確かめる。
     差集合の根拠は`実行した公開工程`の残条件・観測手段か、未達条件・実際の観測結果へ対応付け、可否は`agent-toolkit:atk-mode/playbooks/process-wi-observation.md`「延期adoptと反映後の観測」に従う。
     扱いは`agent-toolkit:atk-mode/playbooks/process-wi-lanes.md`「採否の確定と終了区分」に従う。
   - 延期`adopt`の対象があれば、全ての対象を指定して`atk wi show <ファイル名>... --summary-only --target-repo=<対象リポジトリの絶対パス> --skip-pull`を1回実行する。
     見出し`### <ファイル名> [<状態>]`の状態が、`延期後にadoptしたAWI`の項目では`adopted`、差集合の項目では`processing`であることを確かめる。
3. 未終了の項目を取りこぼさないため、全ての確認の後に、延期の対象から`延期後にadoptしたAWI`を除いた集合を`agent-toolkit:atk-mode/playbooks/process-wi-observation.md`「延期adoptと反映後の観測」へ渡す。
   検収した公開状態の4項目は`agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`「公開状態の検収と再起動」へ渡す。
   続行不能の返却でも、割り当てたリリース判定と残る工程を既存の引き継ぎへ戻す。
   途中で主作業ツリーや対象refの変更か外部からの更新を見た場合は、同節に従って現在の状態を取り直す。
4. 未終了の集合の処置の後、`agent-toolkit:atk-mode/playbooks/process-wi-observation.md`「報告前に確かめる保存状態」を実行して今回の全ての処置を現在のキューへ対応付ける。
   確かめた保存状態を報告の入力にしてから、`agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`「完了報告と終了」に従う。
