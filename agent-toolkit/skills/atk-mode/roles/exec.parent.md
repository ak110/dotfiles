# レーン担当の起動と受領

```text
起動対象: exec.subagent.md
```

メインがレーン間の調停とユーザー判断に集中できるよう、レーン内で完結する計画から統合までをレーン担当へ任せ、その返却を受けるための文書である。
役割`exec`を起動し、返却を受ける側の文書である（担当の手順は`agent-toolkit:atk-mode/roles/exec.subagent.md`にある）。
起動するのは、作業`process-wi`の複数レーンのメイン（`レーン担当`）、修正担当を起こすレーン担当とsingle-laneのメイン（`レビュー修正担当`、`CI修正担当`）、CI失敗を直させる公開担当とリポジトリ担当（`CI修正担当`）である。
レーン担当は計画、実装、実行レビューの起動と収束、統合までを同じthreadで行い、メインは返却の受領、ユーザー判断の中継、レーン間の競合と依存の調停、統合の許可を担う（`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`）。
返却を受けた後の手順は`agent-toolkit:atk-mode/playbooks/lane-receipt.md`にある。

## 入力

<!-- input-contract:exec:start -->
<!-- 生成元: skills/atk-mode/roles/exec.subagent.mdのrequired_inputs・optional_inputs。手動編集しない。 -->
入力の定義はこの起動契約とagents_serverの`start`の検査が所有する。
役割固有の入力（`extra_params`）はこの契約の入力一覧で完結する。
起動の準備で`<役割名>.subagent.md`の本文は読まず、入力はこの一覧で決め、形式の確認は`start`に任せる。
通常起動の前に`agent-toolkit:atk-mode/references/work-contract.md`に従う全体作業契約を用意する。
`agents_server.start`に役割文書の`subagent_md_path`と、以下の役割固有の入力をまとめた`extra_params`を渡す。
`mode`は省略する（明示する場合は`mode: task`）。
全体作業契約の絶対パスは`extra_params`の外の`whole_contract_path`に渡す。

- 必須入力名: `担当種別`、`引き継ぎ記録先`、`書込担当一覧`
- 条件付きの必須入力名（`担当種別`が`レーン担当`の時）: `所有登録`、`統合先worktree`、`統合先branch`、`レーン開始時刻`
- 任意入力名: `選定結果の出力先ファイル`、`レーン識別子`、`所有登録`、`統合先worktree`、`統合先branch`、`レーン開始時刻`、`固有指示`、`上流投入結果`、`再開位置`
- 任意入力名: `レビュー指摘管理表`、`レビュー参考意見`、`CI記録`、`環境構築`、`先行調査記録`

<!-- input-contract:exec:end -->

レーンの資源の作成と、起動前に確かめる準備の結果は`agent-toolkit:atk-mode/playbooks/process-wi-lane-resources.md`「レーン資源の作成・再利用・回収」にある。
`agents_server`の`start`を次の引数で呼ぶ。

| 引数 | 値 |
| --- | --- |
| `cwd` | 対象worktreeの絶対パス |
| `subagent_md_path` | `exec` |
| `extra_params` | 次の各入力 |
| `model_type` | 対象WIの該当レーンの`担当モデル.実装担当`（ユーザー由来の場合だけ指定し、それ以外は省く）。指定値と実行値の比較と記録は`agent-toolkit:atk-mode/references/model-settings.md`「工程別モデル設定」に従う |
| `model_override` | 上記の指定がある場合は `user_instruction` |
| `model_override_evidence` | 上記の指定がある場合は原文・回答の所在（`agent-toolkit:atk-mode/references/model-settings.md`「本人指定による上書き」） |

- `担当種別`: 通常は`レーン担当`、公開工程のCI失敗の修正では`CI修正担当`、同じthreadを続けられないレビュー修正では`レビュー修正担当`とする。
  統合だけを担う担当を新しく起こす場合も`レーン担当`とする（統合の手順は担当が`agent-toolkit:atk-mode/roles/lane-integration.subagent.md`で読む）。
- `選定結果の出力先ファイル`: メインが選定結果を書いたファイルの絶対パス。
  複数レーンの担当の境界もこのファイルから確かめさせ、担当表を複製せずにこのファイルを参照元とする。
- `レーン識別子`: メインがレーンに割り当てた識別子。
- `所有登録`: `atk lane create`が返した所有登録ファイル（`record_path`）の絶対パス（`agent-toolkit:atk-mode/playbooks/process-wi-lane-resources.md`「レーン資源の作成・再利用・回収」）。
- `統合先worktree`: 主作業ツリーの絶対パス。
- `統合先branch`: ベースbranchの名前（`atk lane create`の`--base-branch`の値）。
- `レーン開始時刻`: 最初のレーン担当を起動する直前にメインが記録したタイムゾーン付きの時刻（`agent-toolkit:atk-mode/playbooks/process-wi-lane-resources.md`手順6）。
  `所有登録`、`統合先worktree`、`統合先branch`、`レーン開始時刻`は`担当種別`が`レーン担当`の起動（統合だけを担う担当の起動を含む）で必須で、担当の交代と再開でも初回と同じ値を渡す。
  作業`process-wi`のレーンで`レビュー修正担当`を起こす場合も、統合の指示を受けて`レーン担当`へ戻れるよう同じ値を渡す。
- `固有指示`: AWIに固有の処理の順序、公開、確認、検証の個別の指示（選定結果の`プロジェクト規範の指定`と上流投入の3行は除く）。
  該当が無ければ省く。
- `上流投入結果`: `上流投入`が`混在`の項目を持つレーンだけに、`agent-toolkit:atk-mode/playbooks/process-wi-additions.md`「上流投入」で受け取った要求ごとの内容、投入先、`ファイル名`、`結果`の組を渡す（選定結果に含まれないため）。
- `先行調査記録`: `agent-toolkit:atk-mode/playbooks/process-wi-lane-resources.md`「後段レーンの先行調査」の記録の絶対パス。
  調査担当の終了を観測し、未完了と失敗の部分を区別して渡す。
  無ければ省き、記録の受領を待たずにレーンを起動する。
- `再開位置`: 中断したレーンの再開と`レビュー修正担当`の起動で、`~/.claude/plans`の計画ファイルの絶対パス（公開の時点で計画なしで進行中のレーンでは`計画なし`）。
  複数の計画を持つレーンでは、全ての計画、対応するAWI、保存状態を持つ引き継ぎ記録も渡す。
  初回の起動と、選定の項目別の再開（`反映後の観測だけが残る`で始まる観測のみの再開を含む）では、担当が選定結果から読むので省く。
- `レビュー指摘管理表`: `レビュー修正担当`を新しく起こす時に、未解決の指摘を含む全ての計画の表の絶対パス（複数はJSON配列）。
- `レビュー参考意見`: `agent-toolkit:atk-mode/playbooks/exec-review-loop.md`「レビュー修正」で確かめた疑問と理由を指摘ごとに対応付けた本文か、既存の記録の絶対パス。
  省略と行の不在は参考意見なしとし、同じthreadの継続と新規の起動のどちらでも指摘の原文と区別して渡す。
- `環境構築`: 専用worktreeの環境の準備状況。
  `atk lane create`の準備処理と保存出力から`完了済み`、または準備不要と判断した旨を書く。
  CI修正などで既存の専用worktreeへ起動し、環境構築を担当に任せる場合は`レーン担当が実行`とする。
- `書込担当一覧`: 重複の無い実際のsession IDのJSON配列。
  統合先のメインと合意済みの担当を初回に入れる。
  担当は自分のIDを自分で加えるので、メインは起動の結果の`session_id`を自分の記録の一覧へ加えるだけで追送しない。
  担当の交代、再開、単一レーンへの追送の時に更新し、担当範囲との対応を保つ。
- `引き継ぎ記録先`: `agent-toolkit:atk-mode/references/delegation-prompt.md`「引き継ぎ記録先」に従う。
  `再開位置`を渡す再開、`レビュー修正担当`の起動、統合するレーン担当の新規の起動では、先行の記録をそのまま使わせるため`（継続）`とする。
- `CI記録`: `CI修正担当`だけに渡す。
  `agent-toolkit:atk-mode/playbooks/process-wi-ci-fix.md`の7項目を1つの値にまとめ、全項目がそろうことを起動前に確かめて委譲プロンプトへ直接書く。
  `CI修正担当`への入力からは`選定結果の出力先ファイル`、`レーン識別子`、`固有指示`、`再開位置`、`所有登録`、`統合先worktree`、`統合先branch`、`レーン開始時刻`を省く。

対象worktree、複製元、プロジェクト規範は担当が`cwd`とGitの状態から特定するので、入力から省く。
対象リポジトリへのpushは委譲元が担い、private-notesでの`atk plans commit`と`atk wi`による保存・公開はレーン担当が行う。
両者を混同して一律にpushを禁じると、WIの`adopted`・`rejected`だけが未公開のまま残る。

継続の指示は、同じthreadへ`send_message`の`instruction`と名前付きの`extra_params`で送り、その指示に必要な値だけを渡す（初回の入力は担当が保持する）。
組み込みの委譲機構では`agent-toolkit:atk-mode/references/delegation-prompt.md`「委譲プロンプトの構成」で本文を作る。

## 読込表

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 担当の返却（計画準備、修正担当の実装完了、質問への回答の下書き、続行できない理由、統合準備完了、統合完了）を受領する前 | `agent-toolkit:atk-mode/playbooks/lane-receipt.md` |
| 統合の返却が、mergeの競合で必要な判断か、統合先との組合せだけで起きた変更範囲の検証の失敗への是正commitを示す時 | `agent-toolkit:atk-mode/playbooks/process-wi-lanes.md` |
| 統合の返却で検証結果の警告の判定を受領する時 | `agent-toolkit:atk-mode/references/lane-integration-format.md` |

<!-- return-contract:exec:start -->
<!-- 生成元: skills/atk-mode/roles/exec.subagent.md「出力」。手動編集しない。 -->
## レーン担当の返却契約

全てのcommitと変更範囲の検証を終えた担当は、実装の結果を次の形式でまとめる。
レーン担当は、これを自分が実行レビューを起動する入力に使い、メインへは送らない（`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`「継続する工程」手順3）。
実装だけを委譲された担当（`CI修正担当`など）は、起動した担当へこの形式だけを返す。

```text
実装完了
検証結果: <引き継ぎ記録に書いた検証結果の原本（実行した変更範囲の検証コマンド・終了コード・要点と検証HEADの完全OID、警告の有無、受入シナリオ別のテスト名と合否、保存した両出力と手動観測の絶対パス、直接影響範囲の探索成果物）の所在と原本にない判定上の補足。原本を参照できない時はこれらを直接書く。ユーザーがWIへ直接記入した質問への回答下書きの絶対パス。保存後はUWIのファイル名も記録>
画面差分: <あり（変更した画面資産の相対パスの列挙）|なし>
同種の既存画面: <画面・部品、状態を出す操作、再利用・相違と理由 | なし（検索語と範囲）>
```

画面差分があるか、画面資産を変えずに画面の仕様を変えた場合は、`同種の既存画面:`が必須となる。
既存機能の仕様変更では、全画面・共通部品の全呼び出し元への適用の有無と、未適用の理由を同じ行に含める。
それ以外は省略でき、行が無ければ非該当とする。

初期選定が複数レーンの場合は、`agent-toolkit:atk-mode/playbooks/process-wi-execution.md`「継続する工程」手順5で統合入力を確定した後、次の形式で`統合準備完了`を返して`統合許可`を待つ。

```text
統合準備完了
統合区分: <マージあり|マージなし>
統合入力HEAD: <統合入力HEADの完全OID。マージなしでは「なし」>
UWI保存状況: <原稿の絶対パスと保存済みUWI名を対にしたJSON配列。例: [{"原稿":"/absolute/answer.md","UWI":null}]。未保存はnull、該当なしは[]>
```

終了の結果では、担当モデル、起動候補、物理記録から観測した`engine`、`model`、`effort`を分けて報告する。
不一致は各値とその由来を引き継ぎ記録へ残し、観測できなかった値は未確認とする。

工程の区切りでは、本書の役割固有の行と、`agent-toolkit:atk-mode/playbooks/subagent.md`「返却形式の受け渡し」を使う。
<!-- return-contract:exec:end -->

## 条件と行動

受けた返却か場面ごとに、`agent-toolkit:atk-mode/playbooks/lane-receipt.md`の次の節へ進む。

| 返却か場面 | 進む節 | 送る継続の指示 |
| --- | --- | --- |
| `計画検査完了` | 「計画準備の受領」 | 通知には返信しない。全組`マージなし`の返却は統合の指示へ進む |
| `計画作成完了`、`計画なし準備完了`の返却 | 「計画準備の受領」 | `実装開始`か`実装なし` |
| `CI修正担当`の`実装完了`、`レビュー修正担当`の`対応完了`・`対応完了（再レビュー不要）` | 「実装完了の受領」 | なし（再レビューかCIの確認へ戻る） |
| WIへ直接書かれた質問への回答の下書き | 「レーン担当が確定した質問への回答」 | なし（保存したUWIのファイル名を返す） |
| `続行できない理由`、確認事項、完成条件の変更の判断要請 | 「エスカレーションの受領」 | 必要な回答だけ |
| `統合準備完了` | 「統合の許可」 | `統合許可` |
| 統合を指示する時と`統合完了` | 「統合の指示と受領」（統合の入力`統合区分`、`実行レビュー済みHEAD`、`計画ファイル名一覧`、`AWI終了区分`、`書込担当一覧`の値と、`統合完了`の形式を含む） | `統合開始` |

レビュー指摘への対応を同じthreadへ指示する`レビュー指摘の対応をせよ`は、`agent-toolkit:atk-mode/playbooks/exec-review-loop.md`「レビュー修正」に従う。
レーン担当自身の`実装完了`は、レーン担当が自分で実行レビューを起動するための区切りである（メインが受ける`実装完了`は修正担当の返却に限る）。
