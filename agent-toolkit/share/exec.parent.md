# レーン担当の起動と受領

```text
起動対象: exec.subagent.md
```

メインが`agent-toolkit:process-wi`のレーンを実行する際、本書に従ってレーン担当を起動し、計画あり・なしの準備結果、実装完了およびエスカレーションを受領する。
公開工程で終端担当が`CI修正担当`を起動する場合と、実行レビューの修正担当を新しく起動する場合も本書に従う。
レーン担当は計画の起草、実装、変更範囲の検証、commit、レビュー修正、統合を同じthreadで担う。その手順は`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`が定める。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/process-wi/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec.subagent.md` | 判断 | `read:agent-toolkit:*/share/exec.subagent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/runtime-routing.md#「工程別モデル設定」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/runtime-routing.md#「工程別モデル設定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `lane-integration.subagent.md` | 判断 | `read:agent-toolkit:*/share/lane-integration.subagent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/base-contract.md#「引き継ぎ記録先」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/base-contract.md#「引き継ぎ記録先」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/bugfix/references/ci-failure-handling.md` | 判断 | `read:agent-toolkit:*/skills/bugfix/references/ci-failure-handling.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review.parent.md#「起動方法別のレビュー基準」` | 判断 | `section:agent-toolkit:*/share/exec-review.parent.md#「起動方法別のレビュー基準」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/delegation/references/sending.md#「委譲プロンプトの構成」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/sending.md#「委譲プロンプトの構成」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `single-lane-execution.md` | 判断 | `read:agent-toolkit:*/share/single-lane-execution.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/SKILL.md#「確認要否の判定」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「確認要否の判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec.subagent.md#「入力」` | 判断 | `section:agent-toolkit:*/share/exec.subagent.md#「入力」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/wi-standards/SKILL.md#「由来と承認」` | 判断 | `section:agent-toolkit:*/skills/wi-standards/SKILL.md#「由来と承認」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec.subagent.md#「実装」` | 判断 | `section:agent-toolkit:*/share/exec.subagent.md#「実装」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/user-confirmation-and-report/SKILL.md#「手段の選択」` | 判断 | `section:agent-toolkit:*/skills/user-confirmation-and-report/SKILL.md#「手段の選択」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/wi-standards/SKILL.md` | 判断 | `read:agent-toolkit:*/skills/wi-standards/SKILL.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review.parent.md#冒頭` | 判断 | `section:agent-toolkit:*/share/exec-review.parent.md#冒頭` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review.parent.md` | 判断 | `read:agent-toolkit:*/share/exec-review.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `usability-review.parent.md` | 判断 | `read:agent-toolkit:*/share/usability-review.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review.parent.md#「レビュー修正」` | 判断 | `section:agent-toolkit:*/share/exec-review.parent.md#「レビュー修正」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review.parent.md#「返却に含まれる既存不良の受領」` | 判断 | `section:agent-toolkit:*/share/exec-review.parent.md#「返却に含まれる既存不良の受領」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `review-loop-coordination.md#「エスカレーションの事由別の扱い」` | 判断 | `section:agent-toolkit:*/share/review-loop-coordination.md#「エスカレーションの事由別の扱い」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../skills/check-execution/references/diagnostics.md#「読む出力」` | 判断 | `section:agent-toolkit:*/skills/check-execution/references/diagnostics.md#「読む出力」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `lane-integration.subagent.md#「検証結果の警告の判定」` | 判断 | `section:agent-toolkit:*/share/lane-integration.subagent.md#「検証結果の警告の判定」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `exec-review.parent.md#「完成条件証拠の受領確認」` | 判断 | `section:agent-toolkit:*/share/exec-review.parent.md#「完成条件証拠の受領確認」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `lane-integration.subagent.md#「出力」` | 判断 | `section:agent-toolkit:*/share/lane-integration.subagent.md#「出力」` |

## 読込表

次の時点または条件が成立したら、対象の操作の前に同じ行の資料を全文読む。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 | 資料選択子 |
| --- | --- | --- | --- | --- |
| 統合を指示する前に統合時の完成条件判定を行う時 | `agent-toolkit:review-standards`の`references/exec-review-recording.md`「統合時の完成条件判定」 | 判断 | `section:agent-toolkit:*/skills/review-standards/references/exec-review-recording.md#「統合時の完成条件判定」` | |
| レーン担当の統合の返却が、rebase進行中の競合か、統合先との組合せだけで生じた変更範囲の検証失敗の是正commitを示すとき | `agent-toolkit:process-wi`の`references/integration-failure-receipt.md` | 判断 | `read:agent-toolkit:*/skills/process-wi/references/integration-failure-receipt.md` | |

## 入力

初回のレーン資源は`skills/process-wi/references/run-lanes.md`「レーンと資源」の`atk lane create`から受領する。終了コード0、readyと環境準備の保存済み出力を検収してから起動し、失敗では登録と成果を保持して同じcreateの再開へ戻る。セッションのmanaged-temp直下の`parent-handoff.md`は選定原本・資源の`record_path`・担当thread・引き継ぎ記録・累積計画と工程境界を対応付ける。担当の`cwd`はその資源のworktreeとし、生成側が持つ資源情報と選定項目を委譲入力へ再掲しない。

レーン担当は`agents_server`の`start`を次の引数で呼んで起動する。

| 引数 | 値 |
| --- | --- |
| `cwd` | 対象worktreeの絶対パス |
| `subagent_md_path` | `exec` |
| `extra_params` | 後掲の各項目 |
| `model_type` | 選定結果の当該レーンの`担当モデル.実装担当`。指定が無ければ渡さない |

指定値と実際値の比較と記録は`agent-toolkit:delegation`の`references/runtime-routing.md`「工程別モデル設定」の担当モデルの段落に従い、記録先はレーンの引き継ぎ記録とする。

- `担当種別`: `レーン担当`。公開工程のCI失敗修正では`CI修正担当`。同じthreadを継続できずに統合を担う担当を新しく起動する場合も`レーン担当`とする（統合手順は委譲先が読込表から`lane-integration.subagent.md`を読む）
- `選定結果の出力先ファイル`: メインが選定結果を書いたファイルの絶対パス
- `レーン識別子`: メインがそのレーンへ割り当てた識別子
- `固有指示`: AWI固有の処理順、公開、確認または検証の指示。選定結果が持つ`プロジェクト規範の指定`と上流投入の3行は除く。無い場合は省略する
- `上流投入結果`: `上流投入`が`混在`の項目を持つレーンだけに送る。`skills/process-wi/references/run-lanes.md`「上流投入」で受領した要求ごとの要求、投入先、`ファイル名`、`結果`の組（選定結果に含まれないため）
- `再開位置`: 中断したレーンを再開する場合に、`~/.claude/plans`に残る計画ファイルの絶対パス。複数計画のレーンでは全計画・対応AWI・保存状態を持つ引き継ぎ記録も渡す。初回起動と初回選定の項目別再開（`反映後の観測だけが残る`で始まる観測のみの再開を含む）では、担当が選定結果から読むため入力から除く
- `環境構築`: 専用worktreeの環境の準備状態。`atk lane create`のprepareと保存済み出力から`完了済み`、準備不要と判定した場合はその判定を書く。作成と準備の完了を確定できない場合は担当を起動せず、登録済み資源の準備を回復する。CI修正など既存の専用worktreeへの起動で環境構築を担当へ割り当てる場合は`レーン担当が実行`
- `書込担当一覧`: 重複のない実session IDのJSON配列。統合先メインと合意済み担当を初回入力へ含め、起動直後に返された現在のレーン担当のIDを加えて追送する。担当範囲との対応を保持し、担当交代・再開と単一レーンへの追送で更新する
- `引き継ぎ記録先`: `agent-toolkit:delegation`の`references/base-contract.md`「引き継ぎ記録先」に従う。`再開位置`を渡す再開、`レビュー修正担当`の起動、統合を担う担当の新規起動では先行する記録を引き継ぐため`（継続）`とする

`CI修正担当`には`選定結果の出力先ファイル`、`レーン識別子`、`固有指示`、`再開位置`を渡さず、`CI記録`を委譲プロンプトへ直接記載する。`CI記録`の項目は`agent-toolkit:bugfix`の`references/ci-failure-handling.md`が定め、1つの入力値へまとめ、全項目がそろうことを起動前に確かめる。

対象worktree、複製元、プロジェクト規範などは委譲先が`cwd`とGitから解決する。
対象リポジトリへの直接pushは委譲元が担うが、private-notesでの`atk plans commit`と`atk wi`による保存・公開はレーン担当に残る。両者を同じpush禁止として伝えると、WIの終端だけが未公開になる。

## 計画準備の受領

定型継続は`send_message`の`instruction`と名前付きの`extra_params`から生成する。
`実装開始`・`実装なし`・`レビュー指摘の対応をせよ`・`統合開始`の宣言が必要とする値だけを渡し、保持済みの初回入力は継続する。レビュー修正では`レビュー指摘管理表`へ単一の絶対パスか全計画の表の絶対パスのJSON配列を1回で渡す。ラウンドは各表から解決する。計画ごとの成果物の受け渡しは`exec-review.parent.md`「起動方法別のレビュー基準」に従う。
組み込み委譲の本文生成は`agent-toolkit:delegation`の`references/sending.md`「委譲プロンプトの構成」に従う。

初期選定が1レーンの場合は`${CLAUDE_PLUGIN_ROOT}/share/single-lane-execution.md`を適用する。起動時の`固有指示`へ資源記録、統合先worktree・branchを含め、初回起動直後にレーン開始時刻を追送する。メインは定型の準備・実装・レビュー・統合開始の往復を省き、ユーザー判断の通知と最終の統合結果を受領する。「統合の指示と受領」の統合前検収はレーン担当が行い、メインは返却結果を検収する。

レーン担当は`計画検査完了`の通知後は待たずに実装へ進むため、メインは`実装開始`を返さない。通知の`計画ファイル:`・`統合区分:`・`AWI:`の組（初回に分けた計画では計画の数だけ）を記録して累積し、計画名とAWI集合の重複を拒否し、和集合が選定結果の対象レーンと一致することを確かめる。追加した処理対象WIでは先行する凍結済み計画とその統合区分を引き継ぎ記録から保持する。`plan-check`が集合一致と人間由来行の根拠欄の充足を確認済みであることを受領条件とする。

`計画なし準備完了`では、AWI集合が対象レーンと一致することを確かめて統合区分を記録し、引き継ぎ記録の明示禁止条件と採用手段を比べる。採用手段が禁止条件に当たらなければ同じthreadへ`実装開始`、`マージなし`では`実装なし`を返し、当たる場合は差異を添えて差し戻す。計画なしの実装はWIの要求と完成条件を実行レビューの基準とする。

`計画作成完了`では、作成した計画ファイルの絶対パス（`計画ファイル:`行が計画の数だけ並ぶ）が返る。各計画のAWI集合を計画メタ情報`関連WI`から取得し、計画の絶対パス、AWI集合、保存状態を引き継ぎ記録へ累積する。
メインは各計画の`## 概要`と`## 実施内容`を読み（原則この2節。努力目標。メインのコンテキストを保つ）、次を確認する。満たさない行は差異を添えて同じレーン担当へ差し戻し、その計画には`実装開始`を返さない。

- 要求単位の由来と確認要否は、`agent-toolkit:user-confirmation-and-report`「確認要否の判定」で根拠と既存認可を検収し、必要な確認を発行する。受領後の由来の扱いは`exec.subagent.md`「入力」の由来の確定時点に従う
- `由来`が`人間由来のWI`か`ユーザー指示`の行では`根拠`が原文の要求単位ごとの分解を持つ。その行の`採否`が`部分採用`か`不採用`なら、`agent-toolkit:wi-standards`の`## 由来と承認`が求めるユーザーの明示承認の成立を`根拠`から読み取れる。確認を要する縮小はユーザーの確認を経て確定する
- ユーザーがAWIの`## ユーザーコメント`かUWIの`## 回答`へ処理側に宛てて直接記入した質問があれば、`## 実施内容`に回答をUWIへ記録する工程がある（AWI本文へ転記された質問は対象外）
- ユーザーの依頼かWIが明示した禁止操作、外した機構、許容しない副作用を`## 実施内容`から抽出し、採用手段がそれに当たらないこと。同節だけから両者を特定できない場合も満たさない
- 長い比較調査を含む行は、調査の開始条件と通常修正との関係（並行か、直列にする理由）を持つ
- レーンの統合区分を`## 統合の指示と受領`の確定規則で確定する

メインはエージェント向け文書を含む書込先を名指ししない。書込の認可の境界はWIの要求であり、計画に無いファイルの追加はレーン担当が記録し、実行レビューが差分と記録から判定する（`exec.subagent.md`「実装」）。メインが受け取るのは、WIの要求を超える変更の判断と、未統合の他レーンと重なる書込対象の追加（選定結果への保存と定義が交わる場合の調停）だけである。生成物は原本の改訂後に再生成する。

確認後、同じthreadへ`実装開始`または`実装なし`を返す。`実装なし`のレーンは実装と実行レビューを行わず統合指示へ進む。
`計画検査完了`が`マージなし`を示す計画には何も返さず、同じレーンの他の計画の実装有無が確定してから統合を指示する。

## レーン担当が確定した質問への回答

レーン担当から、ユーザーがWIへ直接記入した質問（AWIの`## ユーザーコメント`かUWIの`## 回答`）への回答下書きの絶対パスと根拠を受け取る。下書きは実行レビューと読者別探索へ入力し、レビュー収束後・統合指示の前に、最新の下書きを`agent-toolkit:user-confirmation-and-report`「手段の選択」と`agent-toolkit:wi-standards`に従って回答ごとのUWIへ保存する。保存するのはレビュー済みの最新本文とする。
`実装なし`と`マージなし`のレーンでは、`rules-main.md`の事実主張の基準で下書きの言い切りを根拠と現物で確かめてから保存し、確かめられない主張はレーン担当へ戻す。
保存したUWIのファイル名を同じレーン担当へ返し、計画があれば進捗ログへ、なければ引き継ぎ記録へ記録させる。保存に失敗した場合は原因を解消してから実在するファイル名を返す。

## 実装完了の受領

受入シナリオの公開された呼び出し手段・入力・期待結果と実結果の対応を受領し、検証済み、未実施、条件変更の判断を区分する。
委譲元が所有する実機観測・質問への回答保存・完成条件の変更判断は、申告を受けた時点で担当と再開先を引き継ぎ記録へ対応付ける。

レーン担当は全実装のcommit後に`実装完了`、`検証結果`、`画面差分`を返す。後2行のいずれかを欠く返却は、実行レビューへ進まず同じthreadへ返却を求める。
完成条件証拠を要求する対象では、`検証結果`から要求単位ごとの未判定検証記録へ到達でき、原文・出所と根拠が記録されていることを確かめ、不足は同じ担当へ返す。このJSONを`exec-review.parent.md`冒頭の`未判定検証記録`としてレビュー担当へ渡す。レビュー担当の完成条件証拠とは保存先と書込主体が異なる。

受領後、対象worktreeの最終実装HEADを対象に`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`に従って計画ごとに実行レビュー担当を起動する（計画なしではWIが基準）。起動の直前に`git merge-base <専用branch> <ベースbranch>`を解決し、`開始時のHEAD`として渡す。`画面差分: あり`では同じ工程で`${CLAUDE_PLUGIN_ROOT}/share/usability-review.parent.md`のユーザビリティレビュー担当も起動する。
実行レビューとユーザビリティレビューの修正はレーン担当の同じthreadへ返す。継続できない場合は`exec-review.parent.md`「レビュー修正」に従い、`担当種別`を`レビュー修正担当`とした新しいレーン担当を起動する。
レビュー指摘への応答が新しい操作、機構または副作用を採用した場合は、前節の禁止条件との比較をやり直し、不適合なら修正を返さず`share/review-loop-coordination.md`のやり取りで是正する。
レビュー収束後は、委譲先から受け取った全事項の確認要否を判定し、`skills/process-wi/references/run-lanes.md`「実装とレビュー」が定める発行と検収を終える（確認不要の報告へUWIを要求しない）。質問への回答の保存と返却も終えてから統合へ進む。
各ラウンドの返却と走査記録に含まれる既存不良は、レーン経由の報告も`exec-review.parent.md`「返却に含まれる既存不良の受領」へ接続する。
統合指示の前に、対象ごとの処置が引き継ぎ記録へ対応付いていることを確かめる。
レビューの未解決0件と、既存不良の処置の完了は別に確認する。

## エスカレーションの受領

続行できない理由を受領した場合は、`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`の`## エスカレーションの事由別の扱い`で区分し、回答だけを同じthreadへ返す。完成条件の変更・除外の判断要請は同節の該当行と同書「技術判断で外せる完成条件」に従う。
統合の返却がrebase進行中の競合か、統合先との組合せだけで生じた検証失敗の是正commitを示す場合は、読込表の`integration-failure-receipt.md`に従う。

## 統合の指示と受領

検証結果の診断・警告と個別・全体の所要は`agent-toolkit:check-execution`の`references/diagnostics.md`「読む出力」の共通基準で受領する。統合前HEADとの比較と統合固有の是正は`${CLAUDE_PLUGIN_ROOT}/share/lane-integration.subagent.md`「検証結果の警告の判定」に従う。

全計画の実装有無から、採用する実装範囲が1件でもあるレーンを`マージあり`、無いレーンを`マージなし`と確定する（計画ごとの`マージなし`は計画に対応付けて保持する）。レーン担当へ統合を委ねる場合は同じthreadへ`send_message`で指示し、`start`を呼ばず、`引き継ぎ記録先`を省いて起動時の値を継続する。継続できない場合の新規起動は`## 入力`に従う。レーン担当が統合する手順は`lane-integration.subagent.md`が定め、メイン自身によるCLI実行も後掲の検収条件を使う。

統合入力HEADは収束したラウンドの`レビューしたHEAD`とし、収束後に再レビューを省いた修正があれば`exec-review.parent.md`「レビュー修正」が保持した修正後のHEADとする。
`マージあり`では、指示の前に次を確かめる。

1. 計画ごとに収束した実行レビューの`受領確認入力のパス`を保持し、`exec-review.parent.md`「完成条件証拠の受領確認」と同じ`--batch`の確認を対象worktreeで再実行する。複数入力は同じオプションを反復し、各生成済み入力をそのまま渡す。非0ならWI名と不足内容をレビュー担当へ返して保留する。各完成条件証拠を計画名とAWI集合へ対応付け、採用予定AWI全体を重複も欠落もなく覆うことを確かめる
2. 全計画の実行レビューが`exec-review.parent.md`「起動方法別のレビュー基準」の統合の開始条件を満たし、各レビュー指摘管理表の未解決指摘が0件である
3. 画面差分がある場合は並列画面レビューが収束し、ユーザビリティレビューの判定が`合格`か`未確定`である。記録が無ければ`reviewer.md`の指摘の種類5（完了条件を満たしていない）として保留する
4. 統合入力HEADが対象worktreeの現在HEADと一致する。実行レビューとユーザビリティレビューの`レビューしたHEAD`は統合入力HEADと一致する。再レビューを省いた修正がある場合は、代わりにレビュー指摘管理表が記録する修正前の短縮OIDと一致する。影響しない計画として引き継ぎ記録へ記録した計画では、`レビューしたHEAD`が記録した`R`と、記録した`H`が統合入力HEADと一致する
5. 読込表の統合時の完成条件判定で受理できない行が残らない

先行レーンの統合でベースbranchが進んでも指示の内容は変えない。rebaseと前後の内容一致の確認はレーン担当が担う。

主作業ツリーへ統合するレーンは、process-wiの1回の実行を通じて同時に1つとする。先に`統合開始`を送ったレーンの返却（`統合完了`か`続行できない理由:`の行）をメイン自身が受け取ってから次のレーンへ送る。判定は受け取った返却本文で行い、主作業ツリーのHEADの前進を根拠にしない。レーン担当はfast-forward後も計画の保存とAWIの終端を続けており、先に次のレーンを始めると古いベースの上でrebaseと検証をやり直すことになるうえ、先行レーンの`変更したエージェント向け文書`を後続の判断へ適用できない。再送と是正レーンの統合も同じ前提で送る。

同じthreadへ次を渡す。実装を伴わないレーンにも送り、計画最終化とAWI終端を行わせる。

- `統合開始`
- `統合区分`: `マージあり`または`マージなし`
- `レーン開始時刻`: メインが最初のレーン担当起動直後に保存した時差付き時刻。担当sessionの置換後も起動時の値を保持する
- `実行レビュー済みHEAD`: 統合入力HEADの7文字以上の一意な短縮OID。`マージなし`では`なし`
- `統合先worktree`: 主作業ツリーの絶対パス
- `統合先branch`: ベースbranch名
- `計画ファイル名一覧`: 引き継ぎ記録に累積した全計画ファイル名の順序付きJSON配列。計画なしのレーンは`[]`
- `AWI終端区分`: AWIごとの確定済みの採否と終端区分。`completed`の上流要求を持つ`混在`の項目（扱いは`skills/process-wi/references/run-lanes.md`「上流投入」）と、観測のみの再開で完成条件を観測できなかったか満たさなかった項目は`終端しない`とする。後者の確認・保留か`processing`での再開かは`run-lanes.md`「採否の確定と終端区分」で判定する。結果は`skills/process-wi/references/finish-session.md`「延期adoptと反映後の観測」で状態へ反映し、満たさなかった項目はメインがセッション終了工程で`inbox`へ戻す
- `プロジェクト固有の公開後の操作の順序`: 指定があるAWIごとの対象と時機。無い場合は送らない
- `完成条件証拠`: `マージあり`の場合に、計画ファイル名（計画なしは`なし`）、対応AWIファイル名の配列、収束した`完成条件証拠のパス`の絶対パスを持つ順序付きJSON配列
- `書込担当一覧`: 初回から更新した現行のJSON配列。現在のレーン担当と統合先メインの実sessionを含む

返却の形式は`lane-integration.subagent.md`の`## 出力`が定める。受領本文はmanaged-tempへ逐語保存し、次を検収する。

1. `保存した計画ファイル`、`adoptを延期したAWIとcommit`、`変更したエージェント向け文書`をJSON parserで解析し、配列であること、必須キーの欠落と余分なキーが無いことを確かめる
2. `統合後のHEAD`が統合先branchの実体と一致する
3. `保存した計画ファイル`が重複の無い文字列配列で、起動時の`計画ファイル名一覧`と順序付きで一致する。保存途中の失敗では`統合完了`を受領せず、引き継ぎ記録の保存済み・未保存の区別から再開する
4. AWIの集合が起動時の終端区分と一致し、延期対象だけが`adoptを延期したAWIとcommit[*].awi`に現れる。実装差分を持つ項目の`commit`は検収済みOIDと、空文字列の項目は充足の根拠と比べる
5. `変更したエージェント向け文書`は重複の無いリポジトリ相対パスの文字列である。メインが既に読んだ文書と残工程で読む文書だけを統合先worktreeから読み直し、後続のレーン判断と公開工程の前に適用する
6. 変更集合が`<役割名>.parent.md`かその`起動対象:`の`<役割名>.subagent.md`を含む場合、両者を同一のplugin rootから原子的に採用する。統合済みworktree側の`<役割名>.parent.md`のrootに全ての起動対象が実在し委譲起動契約の確認に合格する場合だけ、後続委譲の両方をそのrootから解決する。片方の欠落や契約不成立では稼働開始時のplugin rootの両者を使い続け、異なるrootの版を組み合わせない

手順1から5の不成立は同じthreadへ観測値を返して是正を求める。選定原本の統合状態は`atk lane integrate`が全計画保存・WI終端の成立後に更新するため、メインは同じ状態を別操作で保存しない。CLIをメイン自身が実行する場合は、公開ヘルプのmanifest書式へ受理した全計画・WI、独立文脈レビューと採否を渡し、`phase: finalized`を検収する。Git統合後の失敗は所有登録が保持する最終化工程から再開し、未完了argvの補完には`--amend-finalization`を使う。資源は同じthreadへの追加に備えて保持し、回収は全工程と外部プロセスの終了後に、通常の終了処理の`atk lane delete`と、process-loopの子の終了後に行う同じ処理に任せる。
