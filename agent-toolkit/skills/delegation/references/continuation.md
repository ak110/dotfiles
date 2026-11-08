# 担当の継続と交代

本書は、委譲先を同じ識別子で継続するか、新しい担当を起動するかの判定を定める。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| セッション記録を復元・進捗・操作の観測へ使う前 | `agent-toolkit:review-session/references/session-records.md`の「会話の少量取得」 | 判断 | `section:agent-toolkit:*/skills/review-session/references/session-records.md#「会話の少量取得」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `codex-runtime.md#「後続操作の共通先行条件」` | 判断 | `section:agent-toolkit:*/skills/delegation/references/codex-runtime.md#「後続操作の共通先行条件」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/exec-review.parent.md` | 判断 | `read:agent-toolkit:*/share/exec-review.parent.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `../../../share/review-loop-coordination.md#「ラウンド番号の保持先」` | 判断 | `section:agent-toolkit:*/share/review-loop-coordination.md#「ラウンド番号の保持先」` |
| process-wi中の停止や反復の原因が既存不良である時 | `../../process-wi/references/immediate-fix.md#「即時対応」` | 判断 | `section:agent-toolkit:*/skills/process-wi/references/immediate-fix.md#「即時対応」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `agent-tool-continuation.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/agent-tool-continuation.md` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `fallback-launch.md` | 判断 | `read:agent-toolkit:*/skills/delegation/references/fallback-launch.md` |

## 継続と新規起動

担当の交代やモデル・実行手段の変更は、担当の回復として扱う。
停止や失敗反復の原因が既存不良なら、欠陥の処置は回復と別に判定する。
process-wiの実行中は`../../process-wi/references/immediate-fix.md`「即時対応」、それ以外は`agent-toolkit/share/rules/common/01-agent.md`「完遂と先送り」に従う。
可用性の障害と同型のツール入力失敗への切替条件は`fallback-launch.md`「代替起動」に従う。

自身で実行可能な処理を残したままの返却が繰り返される場合は、同一の担当者へ再送を重ねる前に、担当者の交代、作業単位の再編、モデルや実行手段の変更を総コストの観点から比較検討する。
担当者を交代させる際は前任の終了を確認し、検証済みの成果物と未完了の作業内容を後任へ引き継ぐ。
共有されているモデル設定は変更せず、必要な調整は今回の起動時における個別指定にとどめる。
これは通信エラーによる継続不能とは区別して判断する。

- レーン担当者およびCI修正担当者を交代させる場合は、前任の識別子を再利用せず新規に起動する。
  Codexにおいて元の担当者を置き換える新規threadを立ち上げる際は、`references/codex-runtime.md`「後続操作の共通先行条件」を適用してから起動する
- 既存セッションの継続接続は、同一の担当者に対して同一タスクの未完了作業、指摘への対応、再レビューなどを依頼する場合に限り使用する。
  継続する直前に、現在のセッション起動に用いられた`model_type`と、今回委譲する工程の`model_type`が一致していることを確認した上で、同一の`session_id`宛てに`send_message`を送信する。
  engine、model、effortの情報はサーバー側で起動時の値が保持されるため、可用性の都合で候補が切り替わった担当者や、起動後に候補列の設定が変更された担当者に対しても安全に継続できる
- 実際に起動されたルートが`agents_server`である場合は、利用しているengineや実行ホストにかかわらず`send_message(session_id, prompt, timeout)`を使用する。
  サーバー側が実行中ターンのsteer、終了後のreply、保持期限切れ後の暗黙的な再開を適切に選択するため、管理元のプロセスが終了していても再開処理が行われる。
  なお、Claude Codeの`Agent`ツールで起動した担当者に限っては`SendMessage`ツールを使用する（詳細は`agent-tool-continuation.md`「Agentツールの継続」を参照）
- Codexの組み込み委譲において`spawn_agent`から起動した担当者へ追加作業を依頼する場合、終了済みや待機中の担当者には`followup_task`を使用し、現在実行中の担当者へメッセージを追加する場合に限り`send_message`を使用する。
  Codexの`send_message`は担当者のターンを新たに開始する機能を持たず、終了済みの担当者へ送信した内容はキューに滞留する。
  終了済みセッションに対して新規ターンを開始できる`agents_server`の`send_message`とは仕様が異なる点に留意する。
  待機処理は、`followup_task`が正常に受理され、担当者の新規ターン開始または結果返却を観測できて初めて成立する。
  `wait_agent`でタイムアウトが続く場合は、再送する前に`list_agents`で対象の状態を確認する
- `model_type`が一致しない場合、異なる担当者やタスクへ切り替える場合、処理が中断された場合、完了通知を配送できない場合、あるいは前提条件が無効化した場合は、検収済みの状態を引き渡した上で新規にセッションを起動する。
  レビュー指摘への対応では基本的にレーン担当者のセッションを継続し、継続が不可能な場合に限って検収済み状態とレビュー指摘の所在を引き渡してレビュー修正担当者を新規起動する
- 起動後に発生した可用性の障害は`fallback-launch.md`「代替起動」における引き継ぎ起動の手順に従って対処する。
  それ以外の継続不能事象は、実際に継続手段を呼び出した結果に基づいて判定し、`agents_server`では`send_message`が`unknown session: <session_id>`を返した場合のみを継続不能として扱う。
  識別子体系の差異を示すエラーや、`wait`で返される`session retention expired: <session_id>`は継続不能とはみなさない。
  保持していた識別子を見失った場合は、公開の一覧照会や個別照会を用いて復旧を図り、安易に終了や失敗と推測しない
- `send_message`のタイムアウトは、メッセージ配送の成否が未確定であることを示すに過ぎない。
  その継続要求の受理結果、またはその要求によって開始された新規ターンの実行開始を確認できた場合に限って、配送完了と判断して待機を継続する（`wait`の`status=completed`や`agent_message`の文字列一致のみを根拠とすることは避ける）。
  確認が取れない場合は、安易な再送や継続不能の判断をせず、作業を続行できない理由を委譲元へ報告する
- レーン担当者を新たなセッションへ移行させる場合は、検収が完了している直前のcommitの7文字以上の短縮OID（一意に定まるもの）、完了した工程、残りの工程を引き渡す。
  計画ファイル、進捗ログ、保存済みのレビュー指摘管理表などから状態を確認できる場合は、ファイルの絶対パス、対象ID、および未記録の差分のみを渡す。
  記録が存在しない場合は、委譲元がmanaged-temp内にレビュー指摘管理表を作成し、その絶対パスを渡す
- レビュー担当者のセッションが継続できない場合は、同一の`model_type`で新たなレビュー担当者を起動する。
  その際、`レビュー種別: 引き継ぎ再レビュー`、レビュー指摘管理表の絶対パスと`track`、`round: <ラウンド番号>`の行、直前の修正作業による直接影響範囲、読み取り専用の対象範囲を引き渡す。
  実行レビューの場合は、レビュー対象となるHEADの7文字以上の短縮OID（一意なもの）も併せて渡し、`${CLAUDE_PLUGIN_ROOT}/share/exec-review.parent.md`の生成規則に従って`review_contract`を再生成する。
  ラウンド番号は`${CLAUDE_PLUGIN_ROOT}/share/review-loop-coordination.md`の`## ラウンド番号の保持先`に従い、管理表に記載された最大値から継続する。
  新たな担当者は、すでに解消済みの指摘を再調査せず、未解消の行およびそのラウンドで指定された確認範囲のみを対象とする

同一計画の起草から実装、および変更範囲の検証までは、同一のレーン担当者が同一のworktreeにおいて一貫して実施する。
継続が不可能な場合に限って新たな担当者を起動し、検収済みの状態と残りの工程を1つの担当者へまとめて引き渡す。
交代時の会話確認は`../../review-session/references/session-records.md`「会話の少量取得」で全体要求と最新の変更を読み、引き継ぎ記録の判断・残工程と比較する。
