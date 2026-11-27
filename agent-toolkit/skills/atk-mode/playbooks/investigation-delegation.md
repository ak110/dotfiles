# 事象単位の調査の委譲と探索の分担

目的: 委譲元が事実の収集と読む量の多い探索を独立した担当へ分け、費用を抑えながら観測に基づいて原因を特定し処置を決める。
バグ対応、CI失敗の対応、振り返りの事実収集で、事象ごとの観測事実の収集を独立した担当へ委譲する時と、読む量の多い調査や探索を分担する時の手順書である。
方針: 調査は依頼された担当が似た対象から順に進めて知見を再利用し、分担は`agent-toolkit:atk-mode/playbooks/delegation.md`「要否判定」に従う。
担当の推定と対策案は`agent-basics:writing-standards/references/quality-principles-facts.md`「結果の返却」に従い、原因の特定と処置の決定は委譲元が持つ。

## 調査の進め方と委譲時の入力

1. `agent-toolkit:atk-mode/playbooks/delegation.md`「要否判定」に従う。
   担当の分割と似た対象の集約は同書「委譲単位の確定」に従う。
   広い所在の探索は、読み取り専用の`explore`へ分けてよい。
2. 独立した文脈へ渡す入力は、事象、期待する契約、実際の結果、発生条件、ログや資料の絶対パス、対象のcommit、再現手順の7項目とする。
   委譲元の仮説、原因の候補、修正案と、委譲元の推測で調査範囲を限る指示は入力から除く。
3. 報告を受けたら、原因が共通する対象をまとめ、異なる原因を分けて整理する。
   報告された推定と対策案は有力な候補として検証し、原因と処置は委譲元が決める。

## 既存不良の調査担当の起動と受領

```text
起動対象: defect-investigation.subagent.md
```

<!-- input-contract:defect-investigation:start -->
<!-- 生成元: skills/atk-mode/roles/defect-investigation.subagent.mdのrequired_inputs・optional_inputs。手動編集しない。 -->
入力の定義はこの起動契約とagents_serverの`start`の検査が所有する。
役割固有の入力（`extra_params`）はこの契約の入力一覧で完結する。
起動の準備で`<役割名>.subagent.md`の本文は読まず、入力はこの一覧で決め、形式の確認は`start`に任せる。
通常起動の前に`agent-toolkit:atk-mode/references/work-contract.md`に従う全体作業契約を用意する。
`agents_server.start`に役割文書の`subagent_md_path`と、以下の役割固有の入力をまとめた`extra_params`を渡す。
`mode`は省略する（明示する場合は`mode: task`）。
全体作業契約の絶対パスは`extra_params`の外の`whole_contract_path`に渡す。

- 必須入力名: `対象の不良`、`引き継ぎ記録先`

<!-- input-contract:defect-investigation:end -->

自分の作業が持ち込んだのではない既存の不具合を、AWIを起草せずに、見つけたエージェントが元の文脈で直す時に使う。
文脈を分けるのは調査だけで、修正は見つけたエージェント自身が行う。
AWIとして登録して処理する場合（`process-wi`の即時対応、次のセッションへ引き継ぐ不具合など）は、`agent-toolkit:atk-mode/roles/add-wi.parent.md`を使う。
委譲先の手順は`agent-toolkit:atk-mode/roles/defect-investigation.subagent.md`にある。

### 起動方法

`agents_server`の`start`を次の引数で呼ぶ。

| 引数 | 値 |
| --- | --- |
| `cwd` | 調査対象のリポジトリの作業ツリーの絶対パス。見つけたエージェントがその作業ツリーで動いていれば自分の作業ディレクトリ、対象の外で動く場合（常駐起動の統括など）は対象の主作業ツリー（`agent-toolkit:atk-mode/playbooks/process-wi.md`「用語」。all-reposでは対象要素の`worktree`） |
| `subagent_md_path` | `defect-investigation` |
| `extra_params` | 本書「渡す入力」の各項目 |

### 渡す入力

- `対象の不良`: 本書「調査の進め方と委譲時の入力」の7項目（発生条件には症状を観測した所在を含める）。
  見つけた側の仮説、原因の候補、修正案は除く。
- `引き継ぎ記録先`: 省略時と継続時の扱いは`agent-toolkit:atk-mode/references/delegation-prompt.md`「引き継ぎ記録先」に従う。

### 受領と検収

各欄の基準は`agent-basics:bugfix`が定める。

1. `状態: completed`なら、`期待`・`原因`・`対策`・`回帰防止`・`類似見直し`・`再発防止`・`確認`の各欄に具体的な値があることを確かめる。
2. 返却を、示された`分析記録`の箇所とコードやログの現物に突き合わせる。
   `原因`は`agent-basics:bugfix`「記録」の `原因:` に従っていること。
   `対策`に比較して採った理由があること。
   `類似見直し`と`再発`の検収は、`agent-basics:bugfix/SKILL.md`「記録」に従う。
   欄が埋まっていることや独立走査だけで観測点の外側を保証したことにせず、未確認の前提が採否を変える時は、同じ担当へ支持・反証と観測範囲の補完を求める。
3. 不足や食い違いは、`agent-toolkit:atk-mode/playbooks/delegation-receiving.md`「受領後の扱い」で差し戻す。
4. `続行できない理由`の採否は、`agent-toolkit:atk-mode/playbooks/confirmation.md`「確認要否の判定」に従う。
5. 合格した処置に従い、見つけたエージェントが元の文脈で直す。
   修正中に調査の前提と矛盾する観測を得たら、同じ担当へ渡して分析を補わせる。

<!-- return-contract:defect-investigation:start -->
<!-- 生成元: skills/atk-mode/roles/defect-investigation.subagent.md「出力」。手動編集しない。 -->
### 調査担当の返却契約

調査が完了したら、次の形式のみを返却する。

```text
状態: completed
分析記録: <詳細な分析を保存したファイルの絶対パス>
期待: <起きるはずだったことと、その期待が正しく必要である根拠。見直した場合はその結果>
原因: <`agent-basics:bugfix`「記録」の `原因:` に従う>
対策: <無くす・単純にする・直す・足すのどれか、変更する対象と内容。比較して採った理由>
回帰防止: <修正前後で使う試験・検査の案、または行わない理由>
類似見直し: <検索の式と件数、該当した対象ごとの処置。探索の範囲と限界、独立走査を適用した場合の担当識別子と起点の検出の有無>
確認: <修正後に確かめる観測と方法>
再発: <問い6で対策が既にあった場合だけ。前回の対策の所在と種類、効かなかった理由、今回の対策が原因にどう作用するか>
再発防止: <対策の種類の語で始め、採用する策と防ぐ害、または採用しない区分名と理由。同じ害への既存の対策の検索結果>
```

各欄に書く内容は`agent-basics:bugfix`の`SKILL.md`「記録」と、条件が成立した確認観点の資料に従う。
`再発`の欄は、条件が成立しない場合は行ごと省く。

`agent-toolkit:atk-mode/playbooks/subagent.md`「返却形式の受け渡し」に従う。
<!-- return-contract:defect-investigation:end -->

## 類似見直しの分担

調査の責任者は、`agent-basics:bugfix/references/similar-review.md`「探す対象を決める」に従い、判定規則・母集団・判定基準を`agent-toolkit:atk-mode/roles/similar-review.parent.md`の担当へ渡す。
類似見直し担当の工程は`chores`で、原因の判断と結果の採否は責任者が持つ。
`execute`は原因を調べる担当のモデルの区分であり、調査を開発の実行の時点へ一律に割り当てる指定とは別である。
登録時に確定した調査は、対象の変化か新しい証拠が再確認を要する時だけ見直す。

## コンテキスト消費が大きい調査の切り出し

読む量の多い調査では、読み取りだけなら`explore`の起動を優先する（モデルの工程は`agent-toolkit:atk-mode/references/model-settings.md`「工程別モデル設定」）。

1. 委譲元は、既存の公開操作、共通の定義、呼び出し元の参照関係などを先に調べて調査対象の全体像を確定し、所在や接続が不明な部分を探索しておく。
2. 担当の分け方は`agent-toolkit:atk-mode/playbooks/delegation.md`「要否判定」「委譲単位の確定」で決める。
3. 各担当へ、重ならない対象集合、完了条件、判定基準、原本の参照先、返す観測値を示す。
   読み取りの分割でも、`agent-toolkit:atk-mode/playbooks/delegation.md`「作業ツリーの保護」に従う。
4. 担当は必要な原本を自分で読み、割り当てた範囲を自力で終える。
5. 委譲元は全体像と統合の責任を持ち、未確認の対象と結論どうしの矛盾を解いてから判断する。
   切り詰めや上限到達が報告された結果は、網羅、件数、不在の根拠に使わない（原則3、`agent-basics:writing-standards/references/quality-principles-facts.md`）。

全体点検で対象の処理と担当範囲を対応付ける時も、本節に従う。

### 検索結果に基づく探索の分担

件数、ファイル名、要約は、必要な本文へ進む手掛かりとして使う。
要約だけを受け取ったことを理由に原本の確認を省かず、結論を左右する未確定の点は原本で確かめる。

返す報告に含める項目: 委譲した対象と入力、検収で確かめた欄と差し戻し、委譲元が決めた原因と処置、未確認の対象と限界。
