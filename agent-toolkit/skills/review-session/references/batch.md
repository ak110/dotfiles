# 複数セッションの非同期振り返り

記録済みの非活動セッションをまとめて振り返り、共通する原因と対策を整理してAWIを登録する。
分析と参照資料は`session-analysis.md`を共用する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 所要時間の基準を適用する前 | `agent-toolkit:review-session/references/duration-analysis.md` | | |
| 対象の分析を始める前 | `session-analysis.md` | | |
| AWIを起草・独立検収する前 | `agent-toolkit:atk-mode/playbooks/wi-drafting.md`、`agent-toolkit:atk-mode/roles/wi-draft-review.parent.md` | 判断 | `delegation`、`delegation-result`、`read:agent-toolkit:*/skills/atk-mode/roles/wi-draft-review.parent.md` |
| 結果報告の作成・保存に入る前に、メインが読む | `agent-toolkit:atk-mode/playbooks/report-completion.md` | | |

## 起動

環境変数 `AGENT_TOOLKIT_SESSION_REVIEW_BATCH` が無い手動呼出しでは、次の公開操作を実行する。

```sh
atk run-skill --args "batch <追加の着眼点>" agent-toolkit:review-session
```

追加の着眼点が無い場合は`--args batch`とする。
先頭のbatchと残りの着眼点を1つの文字列引数で渡す。
実際の分析はrun-skillが起動する別セッションで行う。
進行中の担当がある場合は、分析をその実行へ引き続き委ねる。
自動起動は前回batch開始から6時間以上経過し、add-awi-by-userを起動した会話が開いていない場合に限る。
手動起動は間隔制限、自動停止設定と未完了の失敗報告AWIによる抑止を適用しないが、活動判定とアカウント共通排他は維持する。
batch自身のセッションも振り返り対象に含める。
非活動の選定対象がbatch自身の記録だけの場合は見送り、通常セッションと混在する場合にまとめて分析する。

## 分析と保存

短い起動目的から本節へ進み、今回のmanifest・自由記述・同一実行識別子・報告先はSessionStartの起動入力を用いる。
中断・再開では同じmanifestの未完了範囲を処理し、成功時だけ手順5のfinishを実行する。
回復可能な残工程は続行し、外部前提不足の終了では未完了対象と再開条件を報告する。

1. `AGENT_TOOLKIT_SESSION_REVIEW_BATCH` が指すmanifest JSONを読む。
   `targets` が今回の固定対象、`prepared` が対象別の準備結果である。
   `target_repo` は今回1件の結果報告UWIの登録先であり、各対象の分析先と区別する。
   準備済みのbundleと文書を再利用し、対象集合と処理範囲をmanifestに固定して分析する。
2. `session-analysis.md`の分析基準・参照資料を読み、対象ごとの `conversation_path` を通読し、候補・統計・比較材料を評価する。
   各対象の `reference_document` があれば読む。
   同じ原因をセッション横断で整理し、対象IDと候補IDの組で出典を保持する。
   `partial_analysis`が真の対象は、読める全行を分析し、欠測を明示した部分分析として完了する。
   `unconfirmed_scope`と`recovered_record_lines`を読み、欠測の記録ID・物理行・件数、回復した後半と失われた断片を結果へ残す。
   所在未解決の記録の内容・件数は不明とし、文脈や操作対応の判断も読めた範囲に限定する。
   これらの項目の省略時は欠測の申告なし、`partial_analysis`の省略時はfalseと解釈する。
   準備自体の失敗、分析未完了や外部前提不足は既存の失敗対応で原因と未完了範囲を報告する。
   実行レビューの採否は、`atk run-script review-adoption -- --since <manifestのstarted_atの7日前>`で直近7日間と前の7日間を集計する。
   明示的な実装中の判断記録と、指摘箇所を`担当の記録:`で書き始めて担当が足した行はレビュー指摘から除外され、除外件数と元の表・行を出力から確認できる。
   判断記録であることが明示されていない過去の行を、集計だけで完全に識別できるとは扱わない。
   不採用率の高いモデルやlevel、同じ理由区分の不採用の反復、前の期間からの増加を分析候補にし、`review-adoption-rejection`の行の管理表を読んで不採用の理由を判断する。
   移管は`transferred`として別に数え、不採用率の分析候補から除く。
   判断主体のラベルが無い旧表は`判断主体不明`として集計する。
   原因・対策・既存AWIの被覆を確定した要求から、手順3の直接起草を進める（`session-analysis.md`「工程5」）。
   モデル不明の表は件数に含めたまま報告する。
   モデル不明の表の割合が前の期間より増えた場合は、限界として報告する前に、不明の表を1件選んで表の名前を含むセッション記録を探し、帰属できない理由を確かめる。
   実行レビュー担当の記録が存在するのに帰属できていない場合は、集計の不具合として扱う。
   記録が残っていない表だけが不明である場合に、その理由を集計の限界として報告する。
3. 原因と対策を確定した要求から、batchのメインが `agent-toolkit:atk-mode/playbooks/wi-drafting.md` に従ってAWIを直接起草する。
   WI投入担当への中継を省き、調査済みの証拠と判断を再利用する。
   同書「直接起草と独立した検収の選択」に従い、`agent-toolkit:atk-mode/roles/wi-draft-review.parent.md`の投入前レビューを経て保存する。
   原因分析を含む原稿の独立検収は省かない。
   全対象の分析と横断整理後に全要求を予告し、合格した原稿を保存する（`session-analysis.md`「工程5」）。
   sourceは `session-review` とし、エージェント由来の提案として処理担当が根拠と採否を懐疑的に評価する。
   所要時間目標、計測の起点と終点、超過分析は`duration-analysis.md`「所要時間の分析と改善」のbatchの定義に従う。
   目標は改善案の必須性だけに使い、対象ごとの必要な調査を完遂する。
4. 終了前の保存漏れを防ぐため、AWIの保存を検収し、対象一覧、登録AWI・見送り・費用確認を対象IDと対応付けて報告本文を確定する。
   部分分析の対象は、その限界と欠測の記録ID・物理行・件数も同じ報告本文に明示する。
5. batchのメインが`agent-toolkit:atk-mode/playbooks/report-completion.md`を読み、その工程で振り返り結果報告書式を使って結果を事後承認型UWIへ1件保存する。
   Claude Codeでは`Skill`で起動し、Codexでは同スキルの`SKILL.md`を絶対パスで取得する。
   sourceは `run-skill`、scopeは `agent-toolkit:review-session batch`、本文frontmatterは `run_skill_report: true` とし、manifestの `target_repo` を `--target-repo` へ明示する。
   同一実行の報告があれば同じUWIを更新して1件に保つ。
   再度report-completionを実行する場合もこの報告を再利用する。
   同スキルの終了処理へ進む前に報告の保存を検収し、次の操作で分析の完了を記録する。

   ```sh
   atk run-script session-review-state -- finish
   ```

   分析完了の記録を保存してからbatchセッションを終了する。

`finish` は分析完了の記録だけを行う。
起動元run-skillがbatchセッションの終了後に報告の同期を検収し、今回準備した範囲を処理済みにする。
この検収と処理済みの確定はbatchセッション自身の完了条件に含めず、分析完了の記録後に終了を要求する。
run-skillの短い目的文はbatchセッション自身が担当する工程の完遂を示し、起動元のこの検収と確定を含まない。
次の対象は自動起動の間隔制限を満たした次回に処理し、手動起動では続けて処理できる。
失敗時はfinishを実行せず、原因と未完了対象を最終応答に残す。
待ち記録、失敗報告AWIの重複確認、AWIが未完了の間の自動抑止は起動元が所有する。

## 状態定義

`_common/session_review.py`はhook・活動状態の削除から独立したOSアカウント共通の`{state_dir}/session-review/`を管理する。

- `pending/<host>-<session_id>.json`: 最上位SessionStartがhost・会話ID・記録パス・cwd・リポジトリ・開始更新時刻・登録世代・batch由来を保存し、処理済み会話のUserPromptSubmitも再登録する。
  委譲先・サブエージェントを除外し、成功確定か取消しまで保持する。
  batch由来のセッションのhookでは再帰的に自動起動しない
- `reviewed/<host>-<session_id>.json`: prepareが増分を選ぶ記録別の最終完全行と同一性を保持し、旧結果が返っても確定範囲を維持する。
  batchの部分分析は欠測情報と、末尾欠測を含む観測済み物理範囲も保持し、準備後の新規追記と区別する。
  部分分析の完了は、欠測を残した範囲の分析と報告の完了を表す。
  元記録がすべて無く、pending・未解決失敗・未確定manifestから参照されない場合だけ回収する
- `runs/`: 実行ID別manifestに開始日時・固定対象・報告先・準備範囲・分析結果・プロセス・失敗を保持し、中断後の報告同期と未確定範囲を検収する。
  開始日時のない旧manifestはmtimeを自動起動間隔の基準にする。
  成功確定済み、または報告同期済み失敗の全対象がpendingから除かれたmanifestとログはrun-skillと同じ30日保持とし、未確定・報告未達は確定まで保持する
- `state.lock`: モデル起動・利用上限待機・WI保存を除く、登録・状態更新・成功確定を短時間排他する
- `failures/`: 起票済みの失敗キーを保持する。
  失敗報告AWIの削除や保存期間の経過だけでは解除せず、終了済みAWIの観測で解除する
- `batch.lock`: 自動・手動の全リポジトリで対象選定から報告同期検収・成功確定まで共有する。
  親中断時もプロセス識別と子の起動引数に含めた固有manifestパスで生存を確認し、二重起動を防ぐ
- 失敗は配布元リポジトリへの是正AWI（`source: session-review`）として報告し、処理担当が是正する。
  UWIでの回答は待たない。
  同原因のAWIが未完了（inbox・processing・hold）の間は自動選定から外す。
  AWIが削除されても同じ配布版では抑止を保ち、配布版が変わった場合は再試行する。
  再試行で同じ失敗キーが発生しても、起票済み記録がある間は新しいAWIを作らない。
  版の無い旧記録は初回の照会時の配布版を基準にする。
  AWIの終了を観測した場合は起票済み記録を解除し、その後の同じ失敗を新たに報告できる。
  手動再試行時も未完了の既存AWIを再利用する。
  準備に失敗した対象だけを切り離し、残りの対象はモデル起動へ進める

失敗AWIには起票ホスト・OS・配布版、対象入力、記録の有無と属性、準備結果、エラー、モデルの終了コードと有限の診断ログ抜粋を含める。
会話本文は転記せず、欠測と切り詰めを明示する。
処理担当は本文の合成入力で修正前の失敗と修正後の成功を検収し、起票ホストでの実再試行を完成条件にしない。
本文から再現条件を復元できない場合だけ、不足する入力を起票ホストへ問い合わせる。

batchはrun-skillの報告同期検収後、手動振り返りは全体分析後の`atk run-script session-review-state -- complete --prepared <準備結果JSON>`で準備済み範囲を確定し、新世代・準備後の追記はpendingに残す。
batchだけが欠測を明示した部分分析を確定し、単発prepareの指定なしでの厳格読込と`--allow-partial-records`、単発completeの未確認拒否は維持する。
起動元は永続scopeへの移管時にも元bundleの所在を保ち、同期確定後に生成時と同一の消費済み全量JSONLだけを回収する。
欠測・回復情報、scope、候補証拠と必要文書は保持する。
`AGENT_TOOLKIT_SESSION_REVIEW_BATCH`はmanifest、`AGENT_TOOLKIT_RUN_SKILL_TARGET_REPO`は報告先を子へ渡す。
`session_review_auto_start`がfalseでも待ちを登録し、SessionStartで件数と手動操作を通知する。
batch起動時の回収では、共有の起動診断`launch.log`も、pending・未確定manifestが無く最終更新から30日経過した場合だけ削除する。
