# 複数セッションの非同期振り返り

記録済みの非活動セッションをまとめて振り返り、共通する原因と対策を整理してAWIを登録する手順書である。
対象と処理範囲は起動時のmanifestに固定し、batchセッションは分析、保存、報告用UWI、分析完了の記録までを担い、処理済みの確定は起動元が担う。
対象ごとの分析は`agent-toolkit:atk-mode/playbooks/review-session-analysis.md`を使う。

## 起動

環境変数`AGENT_TOOLKIT_SESSION_REVIEW_BATCH`が無い手動の呼出しでは、次の公開操作を実行する。

```sh
atk run-skill --args "batch <追加の着眼点>" agent-toolkit:review-session
```

追加の着眼点が無い場合は`--args batch`とし、先頭のbatchと残りの着眼点を1つの文字列引数で渡す。
実際の分析はrun-skillが起動する別のセッションで行い、進行中の実行がある場合は分析をその実行へ委ねる。

- 自動起動は前回のbatchの開始から6時間以上経ち、同じOSアカウントでadd-awi-by-userを起動した会話が開いていない場合に限る。
  入力待ちと登録の完了後もその会話が開いている間は見送り、最後の該当の会話が閉じた後のSessionStart・SessionEndで条件を評価し直す。
- 手動の振り返りと手動のbatchはその間も使え、実行中の振り返りは中断しない。
  手動の起動は、間隔の制限、自動起動の停止の設定、未完了の失敗報告AWIによる抑止を適用せず、活動の判定とアカウント共通の排他は保つ。
- `session_review_auto_start`がfalseでも待ちは登録され、SessionStartが件数と手動の操作を通知する。
- batch自身のセッションも振り返りの対象に含める。
  非活動の対象がbatch自身の記録だけの場合は見送り、通常のセッションと混在する場合はまとめて分析する。

## 分析と保存

短い起動目的から本節へ進み、今回のmanifest、自由記述、同一実行識別子、報告先はSessionStartの起動入力から取る。
中断から再開した時は同じmanifestの未完了の範囲を処理し、成功した時だけ手順5のfinishを実行する。
回復できる残工程は続け、外部の前提の不足で終える場合は、未完了の対象と再開の条件を報告する。

1. `AGENT_TOOLKIT_SESSION_REVIEW_BATCH`が指すmanifest JSONを読む。
   `targets`が今回の固定対象、`prepared`が対象ごとの準備結果である。
   `target_repo`は今回1件の結果報告UWIの登録先であり、各対象の分析先と区別する。
   準備済みのbundleと文書を再利用し、対象の集合と処理範囲をmanifestに固定したまま分析する。
2. 対象ごとに分析の手順書の工程2〜4を行う。
   各対象の`conversation_path`を通読し、`reference_document`があれば読む。
   同じ原因をセッション横断で整理し、対象IDと候補IDの組で出典を保つ。
   - `partial_analysis`が真の対象は、読める全行を分析し、欠測を明示した部分分析として完了する。
     `unconfirmed_scope`と`recovered_record_lines`から、欠測の記録ID・物理行・件数、回復した後半と失われた断片を結果へ残す。
     所在が未解決の記録の内容と件数は不明とし、文脈や操作の対応の判断も読めた範囲に限る。
     これらの項目が無ければ欠測の申告なし、`partial_analysis`が無ければfalseと読む。
   - 準備自体の失敗、分析の未完了、外部の前提の不足は、原因と未完了の範囲を報告する。
   - 実行レビューの採否を`atk run-script review-adoption -- --since <manifestのstarted_atの7日前>`で直近7日間と前の7日間について集計し、不採用率の高いモデルやlevel、同じ理由区分の不採用の反復、前の期間からの増加を分析の候補にする。
     不採用の理由は`review-adoption-rejection`の行の管理表を読んで判断し、移管（`transferred`）は不採用率の候補から除く。
     判断主体のラベルが無い旧表（`判断主体不明`）と、判断記録と明示されていない過去の行は、集計だけで完全には識別できないものとして扱う。
     モデル不明の表の割合が前の期間より増えた場合は、出力の当該期間の`unknown_model_tables`から1件を選び、その表の名前を含むセッション記録を探し、実行レビュー担当の記録があるのに帰属できていなければ集計の不具合、記録が残っていない表だけなら集計の限界として報告する。
3. batchのメインが、工程4の結果を確定した対策の要求と、観測した欠陥の調査の要求に分けてから、`agent-toolkit:atk-mode/playbooks/wi-drafting.md`に従ってAWIを直接起草する。
   WI投入担当への中継を省き、調査済みの証拠と判断を再利用する。
   原因か方式が未確定の調査原稿では、観測事実、再取得できる証拠、仮説、次に確かめる問い、判定する到達点を分ける。
   未確定の方式を採用済みと書かず、投入前レビューでは観測事実から調査の対象と到達点を定められるかを確かめる。
   同書「直接起草と独立した検収の選択」に従い、`agent-toolkit:atk-mode/roles/wi-draft-review.parent.md`の投入前レビューを経て保存する。
   原因分析を含む原稿の独立した検収は省かない。
   全対象の分析と横断の整理の後に全要求を予告し、合格した原稿を保存する（`agent-toolkit:atk-mode/playbooks/review-session-analysis.md`「工程5」）。
   `source`は`session-review`とし、エージェント由来の提案として処理担当が根拠と採否を懐疑的に評価する。
   所要時間の目標、計測の起点と終点、超過の分析は`agent-toolkit:atk-mode/references/review-session-cost.md`「所要時間の分析と改善」のbatchの定義に従う。
4. 終了の前にAWIの保存を検収し、対象一覧、登録したAWI、見送り、費用確認を対象IDと対応付けて報告の本文を確定する。
   部分分析の対象は、その限界と、欠測の記録ID・物理行・件数も同じ本文に書く。
5. batchのメインが`agent-toolkit:atk-mode/playbooks/report-completion.md`を読み、その工程で結果を事後承認型のUWI 1件へ保存する。
   本文は`agent-toolkit:atk-mode/references/report-formats-retrospective.md`の振り返り結果報告の書式で書く。
   `source`は`run-skill`、scopeは`agent-toolkit:review-session batch`、本文のfrontmatterは`run_skill_report: true`とし、manifestの`target_repo`を`--target-repo`へ明示する。
   同一実行の報告があれば同じUWIを更新して1件に保ち、report-completionをやり直す場合もこの報告を再利用する。
   同書の終了処理へ進む前に報告の保存を検収し、次の操作で分析の完了を記録する。

   ```sh
   atk run-script session-review-state -- finish
   ```

   分析完了の記録を保存してからbatchセッションを終える。

`finish`は分析完了の記録だけを行う。
起動元run-skillがbatchセッションの終了後に報告の同期を検収し、今回準備した範囲を処理済みにする。
この検収と処理済みの確定はbatchセッション自身の完了条件に含めず、分析完了の記録の後に終了を要求する。
失敗時はfinishを実行せず、原因と未完了の対象を最終応答に残す。
次の対象は自動起動の間隔の制限を満たした次回に処理し、手動の起動では続けて処理できる。

## 状態定義

batchの状態（待ち、処理済みの範囲、実行ごとのmanifest、排他、失敗の記録）は`_common/session_review.py`がOSアカウント共通で管理し、batchセッションが書くのは`finish`による分析完了の記録だけである。
待ちの登録、対象の選定と固定、アカウント共通の排他、報告の同期の検収、処理済みの確定、失敗報告AWIの重複確認、AWIが未完了の間の自動抑止、状態の回収は、起動元（run-skillとhook）が所有する。

- 処理済みにするのは準備した範囲（manifestの`prepared`と各対象の`scope_path`）だけである。
  準備の後の追記、新しい世代、失敗した範囲は待ちに残って次の振り返りの対象になり、中断の後は同じmanifestの未確定の範囲から再開する。
- 欠測を明示した部分分析を確定するのは、batchの起動元だけである。
  手動の振り返りは、全体の分析の後に`agent-toolkit:atk-mode/playbooks/review-session.md`「工程6」で確定し、未確認範囲を含む準備結果は確定できない。
- カタログ走査は、記録ごとの本文なしの値を`record-index/<runtime>-<走査rootのハッシュ>.json`（`_common/record_metadata_index.py`）へ保存する。
  記録の識別（inode・更新時刻・大きさ）と値を計算するコードの版が同じ記録は解析し直さず、全記録の列挙とstatは照会のたびに行う。
  壊れた索引と別のコードの索引は、解析し直した値で置き換える。
- 準備に失敗した対象だけを切り離し、残りの対象の分析は続ける。
  失敗は起動元が配布元リポジトリへの是正AWI（`source: session-review`）として報告し、UWIの回答は待たない。

返す報告に含める項目: 報告用UWIのファイル名と保存の検収結果、対象ごとの登録したAWIと見送り、部分分析の欠測、`finish`の実行の有無と未完了の対象。
