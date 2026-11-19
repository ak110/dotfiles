# レーンの返却の受領

役割`exec`を起動した側が担当の返却を受領し、統合を許可・指示・受領するまでの手順書である。
起動の入力と返却の形式は`agent-toolkit:atk-mode/roles/exec.parent.md`にあり、受けた返却に対応する節だけを使う。
複数レーンでは、レーン担当が計画、実装、実行レビューの起動と収束、統合前の検収を自分で行い、メインはユーザー判断の中継、レーン間の競合と依存の調停、統合の許可、最終結果の受領を担う（`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`）。
修正担当（`レビュー修正担当`、`CI修正担当`）の返却は、その担当を起動した者（レーン担当、single-laneのメイン、公開担当、リポジトリ担当）が受ける。
返却本文は、`atk agents wait`が保存した`agent_message_path`のファイル（会話が分かる場合はセッションのmanaged-tempの`agents-wait/<session_id>.md`）を、別のファイルへ転記せずに直接読む。
終了条件は、`統合完了`の受領を確定してレーンの資源を回収へ渡した時点（修正担当の返却では、検収を終えて再レビューかCIの確認へ戻った時点）である。

## 計画準備の受領

レーン担当は同じ検収を自分の計画へ適用して進み（`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`「継続する工程」手順1）、メインは待機を伴う返却と`計画検査完了`の通知を受ける。

1. `計画検査完了`の通知では、`計画ファイル:`・`統合区分:`・`AWI:`を計画の数だけ累積し、計画名とAWIに重複が無く、和集合が選定のレーンと一致することを確かめる。
   追加のAWIでも、先行の凍結済みの計画と統合区分を引き継ぐ。
   `plan-check`による集合の一致と、人間由来の行の根拠欄の充足を受領の条件にする。
   レーン担当は通知の後に待たずに実装へ進むので、`実装開始`の返送は省く。
2. `計画なし準備完了`では、AWIの集合が対象のレーンと一致することを確かめて統合区分を記録し、引き継ぎ記録の明示の禁止条件と採用した手段を突き合わせる。
   抵触しなければ`マージあり`には`実装開始`、`マージなし`には`実装なし`を返し、抵触すれば相違点を添えて差し戻す。
   計画を作らない実装では、WIの要求と完成条件を実行レビューの基準にする。
3. `計画作成完了`では、`計画ファイル:`の行ごとに、計画のメタ情報の`関連WI`からAWIの集合を取り、計画の絶対パス、AWIの集合、保存状態を引き継ぎ記録へ累積する。
   各計画のH1直下の要約（8節形式では`## 概要`）と`## 実施内容`を読み、次を確かめる。
   満たさない行があれば、相違点を添えて同じレーン担当へ差し戻し、その計画への`実装開始`を保留する。
   - 要求単位の由来と確認要否を`agent-toolkit:atk-mode/playbooks/confirmation.md`「確認要否の判定」で、根拠と既存の認可から検収し、要る確認をする。
     確定した由来と採否は、前提（目的、現在の方針、利用条件）が変わらない範囲で後の工程でもそのまま使う。
   - `由来`が`人間由来のWI`か`ユーザー指示`の行は、`根拠`欄に原文の要求単位ごとの分解がある。
     その行の`採否`が`部分採用`か`不採用`なら、`agent-toolkit:atk-mode/references/origin-and-approval.md`「由来と承認」が求めるユーザーの明示の承認を`根拠`から確かめられる。
     確認が要る規模の縮小は、ユーザーへ確認してから確定する。
   - ユーザーがAWIの`## ユーザーコメント`かUWIの`## 回答`へ担当宛てに直接書いた質問があれば、その回答をUWIへ記録する工程が`## 実施内容`にある（AWI本文へ転記された質問は対象外）。
   - ユーザーの指示かWIが明示した禁止操作、除外した機構、許さない副作用を`## 実施内容`から抽出でき、採用した手段がそれらの外にある。
     同節だけから両者を特定できない場合も不合格とする。
   - 長時間の比較調査を含む行は、調査の開始条件と通常の修正との関係（並行するか、直列にする理由）を書いている。
   - レーンの統合区分を、本書「統合の指示と受領」手順1で確定できる。
4. レーンの観測が前提を反証した場合は、計画の変更と管理表の結論・根拠から、保つ成果、変える方針、未達、次の判断に残る条件を検収する。
   局所の調査をやり直すための全文の転送は求めず、既存の記録の所在を受け取って、要求・認可と他のレーンへの影響を調整する。
5. メインは個別の編集先（エージェント向け文書を含む）を名指さず、WIの要求を超える変更の可否だけを判断する。
   書込の境界はWIの要求である（`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`「入力と書込所有」）。
   要求の内側の計画外の編集はレーン担当が進め、実行レビューがcommitの差分から妥当性を判定する（`agent-toolkit:atk-mode/playbooks/implementation.md`「実装」）。
6. 確認を終えたら、同じthreadへ`実装開始`か`実装なし`を返す。
   `実装なし`のレーンは実装と実行レビューを行わず、本書「統合の指示と受領」へ進む。
   `計画検査完了`で`マージなし`と示された計画には応答を返さず、同じレーンの他の計画の実装の要否が確定してから統合を指示する。

## 実装完了の受領

修正担当を起動した者が、`CI修正担当`の`実装完了`と、`レビュー修正担当`の`対応完了`・`対応完了（再レビュー不要）`を受ける節である。
レーン担当自身の`実装完了`は、レーン担当が自分で実行レビューを起動する区切りであり、本節の対象外とする（メインへ届いた場合は、実行レビューを自分で起動するよう同じthreadへ返す）。

1. 返却が`検証結果`と`画面差分`の行を持つことを確かめる。
   どちらかが欠けていれば、再レビューにもCIの確認にも進めずに、同じthreadへ再提出を求める。
2. 受入シナリオの公開された呼び出し手段、入力値、期待結果と実際の結果の対応を受け取り、検証済み、未実施、条件の変更に分ける。
   委譲元が担う実機の観測、質問への回答の保存、完成条件の変更の判断は、申告を受けた時点で担当と再開先を引き継ぎ記録へ対応付ける。
3. 完成条件証拠を求める対象では、`検証結果`に各検証コマンドの終了コード、主な確認結果、検証時のHEADの完全OID、受入シナリオごとの結果、保存した観測ログの所在があり、同じ内容が進捗ログ（計画が無ければ引き継ぎ記録）にあることを確かめる。
   不足は同じ担当へ差し戻す。
   完成条件証拠の判定は、その記録を直接読む実行レビュー担当が書く（`agent-basics:writing-standards/references/quality-principles-verification.md`）。
4. 修正の方針で新しい操作、機構、副作用を採用していたら、本書「計画準備の受領」手順3の禁止条件と突き合わせ直し、不適合なら相違点を添えて同じ担当へ差し戻す。
5. 検収を終えたら、`レビュー修正担当`の返却は`agent-toolkit:atk-mode/playbooks/exec-review-loop.md`「レビュー修正」で再レビューへ進める。
   `CI修正担当`の返却は`agent-toolkit:atk-mode/playbooks/process-wi-ci-fix.md`「修正系列」手順4でレビューの要否を判定し、要らなければ再pushしてCIの確認へ戻る。

## レーン担当が確定した質問への回答

WIへ直接書かれた質問（AWIの`## ユーザーコメント`、UWIの`## 回答`）への回答を、統合の指示の前にUWIへ保存してユーザーへ届ける節であり、UWIを保存するメイン（single-laneでは自分）が使う。

1. 回答の下書きの絶対パスと根拠を受け取る。
   下書きはレーン担当が実行レビューと読者別探索へ渡し、メインはその収束の後かつ統合の指示の前に、回答ごとのUWIへレビュー済みの最新の本文を保存する。
2. 実行レビューを行わない`実装なし`と`マージなし`のレーンでは、下書きの断定的な主張を根拠と現物に突き合わせてから保存し（`agent-basics:writing-standards/references/quality-principles-facts.md`）、確かめられない主張はレーン担当へ差し戻す。
3. 保存の手段は`agent-toolkit:atk-mode/references/delivery.md`「手段の選択」に、書式は`agent-toolkit:atk-mode/references/uwi-format.md`に従う。
   保存の手順は`agent-toolkit:atk-mode/playbooks/wi-saving.md`に従う。
4. 保存したUWIのファイル名を同じレーン担当へ返し、進捗ログ（計画が無ければ引き継ぎ記録）へ記録させる。
   保存に失敗した場合は原因を解消し、実在するファイル名を通知する。

## エスカレーションの受領

1. `続行できない理由`を受けたら、`agent-toolkit:atk-mode/playbooks/exec-review-loop.md`「エスカレーションの事由別の扱い」で事由を分け、要る回答だけを同じthreadへ返して、元の受領か検収の節へ戻る。
   完成条件の変更や除外の判断要請は、同節と同書「技術判断で外せる完成条件」に従う。
2. 確認事項、事後承認の対象、人間由来の要求の不採用・部分採用の通知は、`agent-toolkit:atk-mode/playbooks/process-wi-lanes.md`「実装とレビュー」手順4と「採否の確定と終了区分」手順1で扱う。
3. 統合の返却が、mergeの競合で要る判断か、統合先との組合せだけで起きた検証の失敗への是正commitを示す場合は、`agent-toolkit:atk-mode/playbooks/process-wi-lanes.md`「mergeの競合」「統合後の変更範囲の検証の失敗」のメインの段に従う。

## 統合の許可

複数レーンのメインが、`統合準備完了`を返したレーンへ`統合許可`を出す節である。
複数レーンで始めた実行では、稼働中のレーンが1つになっても、レーン担当は`統合許可`を受けてから統合する。
統合先HEADの更新は`atk lane integrate`が統合先ごとの排他で1レーンずつに限るので（`agent-toolkit:atk-mode/playbooks/process-wi-lanes.md`「統合とAWI終了」手順2）、メインは先に許可したレーンの`統合完了`を待たずに許可してよい。
統合前の検収と統合入力の確定はレーン担当が終えているので、メインはその結果をそのまま使う。

1. `統合準備完了`を受けたら、次の3点を確かめる。
   - 選定結果の`段階`と`先行レーン`の依存の条件を満たす（先行レーンの`統合完了`を受領済みである）。
   - 統合先への追随と競合の解消で、各レーンの要求を保てる。
   - `UWI保存状況`の対象のUWIを保存し、ファイル名を同じレーン担当へ返している。
     対象は、本書「レーン担当が確定した質問への回答」の回答のUWIと、`agent-toolkit:atk-mode/playbooks/process-wi-lanes.md`「実装とレビュー」手順4の事後承認のUWIである。
     未保存なら、各節の条件と手順でレビュー済みの最新の原稿を保存し、ファイル名を返してから許可する。
2. 満たしたら、同じthreadへ`send_message`の`instruction: 統合許可`を送る。
   複数のレーンから同時に届いた場合は、依存の順を守って受け取った順に許可する。
3. 許可の後の`統合完了`は、本書「統合の指示と受領」で検収する。

## 統合の指示と受領

```text
起動対象: lane-integration.subagent.md
```

<!-- input-contract:lane-integration:start -->
<!-- 生成元: skills/atk-mode/roles/lane-integration.subagent.mdのrequired_inputs・optional_inputs。手動編集しない。 -->
入力の定義はこの起動契約とagents_serverの`start`の検査が所有する。
起動準備はこの契約の入力一覧で完結する。

- 必須入力名: `統合区分`、`実行レビュー済みHEAD`、`統合先worktree`、`統合先branch`、`計画ファイル名一覧`、`AWI終了区分`、`レーン開始時刻`、`書込担当一覧`
- 任意入力名: `完成条件証拠`、`プロジェクト固有の公開後の操作の順序`

<!-- input-contract:lane-integration:end -->

統合を指示して`統合完了`を受領する節であり、複数レーンのメインが使う。
統合の前にレーン担当が自分で行う検収（手順3）と、メインが専用worktreeを引き取ってCLIを自分で実行する場合にも同じ条件を使う。
統合の入力、manifest、最終化の形式は`agent-toolkit:atk-mode/references/lane-integration-format.md`にある。
統合する担当の手順は`agent-toolkit:atk-mode/roles/lane-integration.subagent.md`にある（所在参照）。

1. 全ての計画の実装の有無を確かめ、採用する実装が1件でもあるレーンを`マージあり`、無いレーンを`マージなし`とする（計画ごとの`マージなし`は計画に対応付けて保つ）。
2. 統合入力HEADには、レビューが収束したラウンドの`レビューHEAD`を使う。
   収束後に再レビューを省いた修正があれば、`agent-toolkit:atk-mode/playbooks/exec-review-loop.md`「レビュー修正」で保った修正後のHEADを使う。
3. `マージあり`では、指示の前に次を確かめる。
   - 完成条件証拠: レビュー単位ごとに受理した最後の実行レビューの返却ファイル（`atk agents wait`の`agent_message_path`）と`レビューHEAD`を保つ。
     `agent-toolkit:atk-mode/references/lane-integration-format.md`「入力」の`完成条件証拠`の確認（AWIの形式と`review_contract`の判定行、採用予定のAWIの被覆）をする。
     欠ければレビューの起動元へ差し戻す。
     内部の群は`agent-toolkit:atk-mode/playbooks/process-wi-lanes.md`「統合とAWI終了」手順1に従い、元のWIの終了には群の局所の判定に加えて同手順の群間の検収を求める。
   - レビューの収束: 全ての計画の実行レビューが`agent-toolkit:atk-mode/playbooks/exec-review-loop.md`「収束と統合への引渡し」の収束の条件を満たし、各レビュー指摘管理表の未解決の指摘が0件である。
     返却と走査記録に含まれる既存の不良の処置（`agent-toolkit:atk-mode/playbooks/exec-review-loop.md`「返却に含まれる既存不良の受領」）が終わっていることも、未解決の指摘とは別に確かめる。
   - 画面差分: 画面差分があれば並行する画面のレビューが収束し、ユーザビリティレビューの判定が`合格`か`未確定`である。
     記録が無ければ、`agent-basics:review-standards/references/reviewer-execution.md`「指摘の分類」の種類5（完了条件を満たしていない）として保留する。
   - HEADの一致: 統合入力HEADが対象worktreeの現在のHEADと一致し、実行レビューの`レビューHEAD`とユーザビリティレビューの`レビューしたHEAD`が統合入力HEADと一致する。
     再レビューを省いた修正があれば、レビュー指摘管理表に記録した修正前の短縮OIDと一致する。
     影響を受けない計画と内部の群では、引き継ぎ記録の`R`が受理済みの`レビューHEAD`と、比較先の`H`が統合入力HEADと一致する。
   - 統合時の完成条件判定: `agent-toolkit:atk-mode/references/review-record.md`「統合時の完成条件判定」で、対象の全ての行が受理の条件を満たす（各`達成`行に所在と観測内容の組がある）。
4. 同じthreadへ`send_message`で`統合開始`と次の入力を送り、`start`は呼ばない（`引き継ぎ記録先`は省いて起動時の値を引き継がせる）。
   実装を伴わないレーンへも送り、計画を最終化させ、AWIの採否を確定させる。
   同じthreadを続けられなければ、`agent-toolkit:atk-mode/roles/exec.parent.md`「入力」に従ってレーン担当を新しく起動する。
   各入力の意味は`agent-toolkit:atk-mode/references/lane-integration-format.md`「入力」にあり、起動側が決める値は次のとおりである。
   - `統合区分`: 手順1の値。
   - `実行レビュー済みHEAD`: 手順2の統合入力HEADの7文字以上の一意な短縮OID（`マージなし`では`なし`）。
   - `統合先worktree`: 主作業ツリーの絶対パス。
   - `統合先branch`: ベースbranchの名前。
   - `計画ファイル名一覧`: 引き継ぎ記録に累積した全ての計画ファイル名の順序付きJSON配列（計画が無ければ`[]`）。
   - `AWI終了区分`: AWIごとに確定した採否と終了区分。
     値と判定は`agent-toolkit:atk-mode/playbooks/process-wi-lanes.md`「採否の確定と終了区分」に、`混在`の項目の扱いは`agent-toolkit:atk-mode/playbooks/process-wi-additions.md`「上流投入」手順3にある。
   - `レーン開始時刻`: 最初のレーン担当を起動した直後に記録した値（`agent-toolkit:atk-mode/playbooks/process-wi-lane-resources.md`「レーン資源の作成・再利用・回収」手順6）であり、担当が替わっても保つ。
   - `書込担当一覧`: 初回から更新してきた最新のJSON配列（現在のレーン担当と統合先のメインの実際のsessionを含む）。
   - `完成条件証拠`: `マージあり`だけに渡す。
     返却本文を組み直さず、手順3で確かめたファイルのパスを渡す。
   - `プロジェクト固有の公開後の操作の順序`: 指定のあるAWIだけに渡す。
5. 先行レーンの統合でベースbranchが進んでも、指示はそのまま保つ。
   最新の統合先のmerge（`target_advanced`を受けた`--restart-review`を含む）、競合の解消、影響範囲の検証は、レーン担当が`agent-toolkit:atk-mode/references/lane-integration-format.md`「マージありの統合」で行う。
6. `統合完了`を受けたら、形式を本節「統合の返却契約」と比べ、指示で渡した値と次の観測を比べる（受領条件）。
   検証結果の診断と警告は`agent-toolkit:atk-mode/references/lane-integration-format.md`「検証結果の警告の判定」で受領する。
   1. 返却が`統合完了`で始まり、規定の各行を持ち、JSON配列の行に重複が無い。
   2. `統合後のHEAD`が、所有登録の`jq -r '.integration.integrated_head' <record_path>`が返す完全OIDの先頭と一致する。
      統合先worktreeで`git merge-base --is-ancestor <統合後のHEAD> <統合先branch>`が終了コード0で終わる。
      後続レーンの短い更新で統合先branchが先へ進んでいても、その子孫であれば満たす。
   3. `jq -r '.integration.phase' <record_path>`が`finalized`を返す（`git-integrated`は最終化の途中であり、不成立とする）。
   4. `保存した計画ファイル`が`計画ファイル名一覧`と順序を含めて一致し、各計画が`atk plans list`の出力に無く、`atk config get private_notes`が返すrootの`plans/`に保存されている。
   5. `adoptしたWI`、`rejectしたWI`、`終了しない`と延期の項目を、`atk wi show <ファイル名>... --target-repo=<統合先worktree> --skip-pull --summary-only`で1回取得する。
      取得のしかたは`agent-toolkit:atk-mode/references/states-and-dependencies.md`「複数件の`atk wi show`の取得」に従う。
      終了0で要求した全ての識別子がそろい、採否に応じて`adopted`か`rejected`であり、`終了しない`と延期の項目は元の状態のままである。
   6. `adoptを延期したAWIとcommit`の各commitが、統合先worktreeで`git rev-parse --verify <commit>^{commit}`で解決でき、`git merge-base --is-ancestor <commit> <統合先branch>`が終了コード0で終わる。
   7. `変更したエージェント向け文書`が、統合先worktreeで`atk run-script agent-doc-changes -- <統合前HEAD> <統合後のHEAD>`を単独で実行した結果と一致する（同じレーンの追加の統合では、前回受理した値との和集合と比べる）。
      統合前HEADには所有登録の`jq -r '.integration.new_base // .integration.verified_base' <record_path>`を使う。
      許可した時点の統合先HEADを使うと、許可から短い更新までに他のレーンが統合先を進めた場合に、範囲へ他のレーンのcommitが入る。
7. 受領条件を満たしたら、意味を確かめる（意味確認）。
   1. `adoptしたWI`の実装commitを`atk run-script plan-commits -- <計画の絶対パス> --worktree <統合先worktree>`で取得する（計画が無ければ`--handoff`と`--allowed-awi`で引き継ぎ記録から取得する）。
      実装commitを持たない項目は、実装差分が無いことを充足の根拠から判断する。
   2. `変更したエージェント向け文書`から、既読の文書、残る工程で読む文書、現在の役割や継続中の工程の契約を変える文書を選び、統合先worktreeから読み直して後の判断と公開工程へ適用する（取得は現在の担当に関係する文書に限る）。
      未配布の採用版に従う必要があれば、`agent-toolkit:atk-mode/references/work-contract.md`「記録する内容」「更新する時点」に従い、従う版の所在、適用対象、解除条件を全体作業契約の暫定規則へ記録して配下へ渡す。
   3. 変更した文書に`<役割名>.parent.md`かその`起動対象:`の`<役割名>.subagent.md`があれば、両方を同じplugin rootから同時に採用する。
      統合済みworktreeの`<役割名>.parent.md`のrootに全ての起動対象が実在し、委譲の起動契約の確認に合格する場合だけ、以後の委譲で両方をそのrootから解決する。
      一方が欠けるか契約が成り立たなければ、起動開始時のplugin rootの両ファイルを使い続け、両者の版を同じrootにそろえる。
   4. 最後の独立したレビューの後に統合中に加わった変更（merge後の検証失敗の是正など）があれば、追加変更、比較元のHEAD、追加変更後のHEADで行った直接影響範囲の検証を照らし合わせる。
      追加変更の検証には、追加変更後のHEADでの成功だけを使う。
      再レビューを省いた追加変更では、変えた定義・文言・構成の変更前の値から消費側を自分でたどり、提出された検証範囲と比べる。
      最後のレビューの後に契約の変更が無ければこの比較は繰り返さず、同じ版・同じ条件の成功を再利用する。
8. 受領を確定する前に、委譲元である自分の既存の作業記録（引き継ぎ記録、進捗記録、作業契約）から、このレーンかそのWIに関係する未処置の中継（例: 成果物の所在を別のレーンへ伝える）と後続の条件（例: 公開後の観測を再開する条件）を探す。
   該当する義務は、ここで実施するか、担当と再開条件を記録に残してから受領を確定する。
   処置済みの記録と、このレーンにもそのWIにも関係しない記録は、確かめるだけで次へ進む。
   探す範囲は既存の作業記録に限る。
   統合の検収の成功と委譲元が記録した義務の完了は別であり、この確認を省いて別のレーンへの中継が漏れた事例がある（`agent-basics:writing-standards/references/quality-principles-ownership.md`）。
9. 受領条件か意味確認が成り立たなければ、観測値を同じthreadへ返して是正を求める。
   途中で失敗した統合は、同じmanifestとレビュー対象のHEADで同じ公開操作から未完了の工程を再開させる（`agent-toolkit:atk-mode/playbooks/process-wi-lanes.md`「統合とAWI終了」手順3）。
   メインがCLIを自分で実行する場合も、独立したレビューと完成条件の判定を通った同じ入力を渡し、本節の受領条件で確かめる。
10. 受領を確定したら、消費する工程を終えた後に`atk lane delete`でレーンの資源を回収する（`agent-toolkit:atk-mode/playbooks/process-wi-lane-resources.md`「レーンworktreeの扱い」）。
    公開工程へは、`agent-toolkit:atk-mode/playbooks/process-wi-publishing.md`冒頭の開始条件が成り立ってから進む（再送と是正のレーンも同じ）。

返す報告に含める項目: 受領した返却と判定（差し戻しと理由を含む）、送った継続の指示、保存したUWI、統合結果（`phase: finalized`と統合後のHEAD）、本書「統合の指示と受領」手順8で見つけた中継と後続の条件とその処置、未達と再開先。

<!-- return-contract:lane-integration:start -->
<!-- 生成元: skills/atk-mode/roles/lane-integration.subagent.md「出力」。手動編集しない。 -->
### 統合の返却契約

`変更したエージェント向け文書`には、統合先worktreeで`atk run-script agent-doc-changes -- <統合前HEAD> <統合後のHEAD>`を単独実行し、終了コード0で取得したJSON配列をそのまま使用する。
統合前HEADは、このレーンの短い更新の直前の統合先HEAD（所有登録の`.integration.new_base`、mergeしなかった場合は`.integration.verified_base`）とする。
`マージなし`の場合は空配列とする。
同一レーンの統合を再度実行する場合は、前回までに返却した値との重複のない和集合を返す（メインエージェントはこの値を用いてレーン全体の規範変更を再取得する）。
返却するたびに、その値を引き継ぎ記録先へ記録する。

次の固定形式のみで結果を返す。

```text
統合完了
統合後のHEAD: <7文字以上の一意な短縮OID>
保存した計画ファイル: <["計画ファイル名",...]形式のJSON配列。計画なしは[]。入力と同じ順序で重複なし>
adoptしたWI: <ファイル名のASCIIカンマ区切り。無い場合は「なし」>
rejectしたWI: <ファイル名のASCIIカンマ区切り。無い場合は「なし」>
adoptを延期したAWIとcommit: <[{"awi":"AWIファイル名","commit":"7文字以上の一意な短縮OIDまたは実装差分が無い場合の空文字列"}]形式のJSON配列。無い場合は[]>
変更したエージェント向け文書: <["リポジトリ相対パス"]形式のJSON配列。無い場合は[]>
```

続行不能と追加報告行は、共通契約`agent-toolkit:/share/rules/rules-subagent.md`「返却形式の受け渡し」に従う。
<!-- return-contract:lane-integration:end -->
