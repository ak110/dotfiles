# Codexでの委譲方法：agents_serverの起動

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| 委譲先と起動手段を選ぶ前 | `runtime-routing.md` |
| 委譲先と起動手段を選ぶ前 | `process-model-settings.md` |
| 同一担当の継続・交代を判断する前 | `continuation.md` |
| 可用性の障害を確認して回復する前 | `fallback-launch.md` |

## agents_serverの起動

`agents_server`の`start`（全`mode`）はCodexネイティブ委譲とは異なる仕組みであり、指定された`model_type`からengine、model、effortを解決して起動する。
通常起動（`task`の通常起動および`delegate`）に対しては、`_agents_server/launch_prompts.py`が`rules-subagent.md`をシステムプロンプトへ追加する。
軽量な探索・書込・shell実行に適用される規範は、`agent-toolkit:delegation/references/delivery-scope.md`にある配送範囲表に記載されている。
この表で配送されない制約事項は、委譲プロンプト側で記述する。
工程別モデル設定のキーが定義されている工程については、`process-model-settings.md`「工程別モデル設定」に従ってengineを解決する。
`engine=claude`の指定をCodexの`spawn_agent`へ勝手に置き換えることは行わず、Claude向けには対応する`model_type`を指定して`agents_server`を使用する。
指定されたengineを実行する手段が存在しない場合は、未完了として返すか、続行不能の理由を報告する。

1. 全体要求を届けるため、Codex環境における役割指定`task`と`delegate`では、初回起動時も含めて、呼び出し側が保持している全体作業契約の絶対パスを`whole_contract_path`へ渡す。
   サーバーが役割の起動設定を解決し、通常起動の場合だけ参照を検証・配送する。
   軽量役割の`task`では指定値を参照せず、契約本文・専用記録を追加しないため、呼び出し側による内部設定の判別は不要である。
   自由本文の`explore`・`write`・`shell`や`handoff_from`による引き継ぎ起動には、この引数を混在させない。
   既存の通常起動での引数省略は、受領済みの参照を継承できる場合に限り互換性維持として許容される。

2. 起動結果を確定するため、起動時に可用性の問題で失敗した場合の候補切り替えはサーバー側に任せ、委譲元は1回の起動応答を受け取る。
   起動後のターンにおいて可用性エラーにより終了した場合は、前任エージェントの終了と書込権限の解放を確認した上で、返却結果の`next_action`に従い、`start`に`handoff_from`のみを指定して引き継ぎ起動する。
   サーバー側が前任の起動条件と記録を後任へ引き渡し、応答として返された後任の`session_id`を以後の待機対象とする。
   引き継ぎ起動の定義は`runtime-routing.md`「実行手段」、可用性障害時の引き継ぎと記録内容は`fallback-launch.md`「代替起動」で定める。

3. 起動後は、継続には`send_message`、中断には`kill`、破棄には`stop`、結果の受領には`atk agents wait`を使用する。
   出力量が大きいコマンドは`start`の`shell`モード、読み取り専用の探索は`explore`モードを、agents_serverのserver instructionsに記載されている採算の目安に従って使い分ける。
