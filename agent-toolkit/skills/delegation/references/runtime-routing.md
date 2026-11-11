# 実行手段の選択

本書は、委譲先を起動する実行手段（`agents_server`とホスト組み込みの委譲機能）の選択と、起動区分の取り決めを定める。
委譲先固有の具体的な作業手順は`<役割名>.subagent.md`側に記載する。

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 実行手段

`agents_server`の`start`のうち、`mode`に`delegate`を指定する起動、および`mode:`を宣言しないか`delegate`を宣言している`<役割名>.subagent.md`による`task`の起動を「通常起動」と呼ぶ。
一方、`mode`に`explore`、`shell`、`write`を指定する起動、およびこれらを宣言している`<役割名>.subagent.md`による`task`の起動を「軽量起動」と呼ぶ。
この区分は、`agent_toolkit/_agents_server/launch_prompts.py`の`LIGHTWEIGHT_LAUNCH_KINDS`と一致させる。
両者は読み込まれる設定や利用可能なツールが異なるため、委譲先の判断に必要なプロジェクト規範が自動で届かない起動形態では、委譲プロンプト側で明示的に渡す。

処理を終了した担当者を「前任」、その作業を引き継ぐ新たな担当者を「後任」と呼ぶ。
`start`に`handoff_from`のみを指定し、前任の起動条件や記録を引き継いで後任を開始する操作を「引き継ぎ起動」と呼ぶ。
引き継ぎ起動では、通常起動と軽量起動の区分や用途を前任からそのまま引き継ぐ。

1. 起動手段は、その手段が提供する終了観測手段とセットで選定する。
     機能と権限が同等であれば、`atk agents wait`によって機械的に終了を観測できる`agents_server`を選択する。
     ホスト組み込みの委譲機能を選んだ場合は、そのホストの委譲一覧におけるステータスや指定した成果物の状態によって終了を観測する

2. Claude Codeからの委譲では、工程別モデル設定のeffortを指定できるため`agents_server`を標準とする。
     組み込みの`Agent`ツールを使用するのは次のケースに限る
     - `agents_server`のMCPツールを呼び出せない場合。
       このときは切り替えた工程、本来の設定値（engine、モデル、effort）、および切り替え理由を報告に記録する
     - `agents_server`のexplore・delegate・taskに同等の手段がない専用agent定義を使う作業（プロジェクト固有の定義など）。
       同等の作業を行えるホスト組み込みの汎用定義はこの例外の対象外とし、次の`mode`で`agents_server`の`start`へ発行する。
       Exploreとweb-fetchは`explore`、general-purpose・claude・`subagent_type`を省いた起動は`delegate`（役割文書があれば`task`）、Planは`delegate`または`task`である。
       同等の手段の有無は`agents_server`の各modeが公開する機能と到達性から判定し、直接のWebFetchなどあるツールを使えないという診断や、ホストが特定の組み込みAgentを案内したことは判定の根拠から外す。
       同じmodeでも実行エンジンによって使える道具が異なるため、判定は工程別設定の候補となる実行エンジン（CodexとClaudeなど）ごとに行い、1つのエンジンの会話で確かめた結果を他のエンジンへ当てはめない。
       対応する`mode`で起動したが同等の結果を得られなかった場合（`explore`がページの本文を取得できないと返した場合など）は、同等の手段がない場合に当たる。
       汎用定義の起動はPreToolUseのhookが遮断し、対応する`mode`での出し直しを通知するため、遮断された場合は通知に従う。
       本項の各ケースに当たる場合は、遮断された`Agent`の呼び出しを同じ入力でもう一度発行すると起動できる
     - ユーザーから明示的な指示がある場合
     - `routing-fork.md`「会話を引き継ぐ委譲」によりforkを選択する場合。
       このときは`subagent_type: "fork"`で起動し、親と同一のモデルおよびeffortを使用する

3. Codexから別のエージェントへ委譲する場合も`agents_server`を標準とし、`spawn_agent`の選択にも前述の共通の例外条件を適用する。
     専用定義の例外は実際にその定義を使用できる場合に限る。
     forkでは`fork_turns`を省略するか`"all"`を指定し、モデルやeffortを上書きしない

4. 汎用のAgent機能にも、前述のClaude Codeの`Agent`ツールを使用する条件を適用する

5. 役割文書が用意されている工程にはその文書を渡す。
     読み取りのみの調査、参照実装に準拠した定型的な書き込み、出力行数が多いコマンド実行などは、それぞれの専用ツールの公開説明を確認した上で適切に割り当てる。
     なお、切り詰めや上限到達が報告された調査結果を、網羅性、件数、または不在の根拠としては扱わない

6. 起動時の可用性トラブル（一時的な障害など）に伴う候補の切り替えはサーバー側に任せる。
     起動後に可用性障害を確認した場合は`fallback-launch.md`「代替起動」を読む。
     実行中の担当者は、経過時間や無応答の長さだけを理由に置き換えず、終了まで観測を続ける

7. 委譲先からの即時通知の手段は`agent-toolkit/share/rules/rules-subagent.md`「確認事項の即時通知」が定め、通知を受領した後の待機手順は`waiting-and-monitoring-intervals.md`「待機区間の構成」に従う

8. 実績として記録する情報は、起動結果として返されたrouteと識別子とする

   fork機能に関する監査記録は、`docs/development/audit-records.md`の「agent-toolkit/skills/delegation/references/runtime-routing.md：実行手段：2026年9月27日」に記載されている
