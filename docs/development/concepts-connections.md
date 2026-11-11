# 方針・意向: 委譲と接続・利用上限

委譲の分担と入力は[委譲の記録](concepts-delegation.md)を参照する。

## 委譲全般と利用上限

現行の規定はルールファイルの「委譲の要否判定」、`agent-toolkit:delegation`および
`agent-toolkit/share/`配下の各タスク文書にある。

単発の委譲は、常設規範の基本委譲契約で対象、読取・書込権限および完了条件を確定し、受領後に成果物、Git状態および検証結果を実際に確認する。
工程別モデル設定、複数主体調整、継続、停滞検知または巻き取りが必要な高度な委譲だけが、`agent-toolkit:delegation`の起動方式ごとの契約を適用する。
実際の別主体への委譲は、Claude Code・Codexとも`agents_server`を標準とし、メインが工程遷移と同一threadの継続を自ら制御する。
組み込み委譲の例外は`agent-toolkit/skills/delegation/references/runtime-routing.md`「実行手段」に従う。
操作と終了観測の説明自体は、通常起動の許可条件を追加しない。
調整専任の中間層を置かず、実際の別主体による独立検証とモデル使い分けを維持する目的は、2026年9月のユーザー指示による。
両ホストの標準手段は、2026年10月28日のユーザー指示で`agents_server`へそろえた。

工程別モデル設定の指定が一時的に利用できない場合、設定値は書き換えずセッション内の起動時指定だけで代替する。
設定はユーザー単位の単一値を全セッションが共有し、書き換えが並行セッションへ波及するためである。
代替の組合せの目安と、effortを指定できない環境でGPT系または上位モデルを選ぶ方針は
`agent-toolkit/skills/delegation/references/fallback-launch.md`へ集約する。

- Claude CodeのWeekly limitと5時間の利用上限で拒否された場合は、解除まで待って同じClaudeで作業を続け、Codexなど別の候補へ切り替えない
  （2026年10月4日、ユーザー指示。待てば確実に解除されるため、切替で作業の文脈を失う方が損失が大きい）。
  対象は`agents_server`、メインのセッション、`atk wi process-loop`の可用性判定、`atk run-skill`の本作業と`atk commit`であり、`overage`、他の429、529、認証・権限の失敗は従来の切替のままとする。
  Claude統括CLIは同じengine内の利用可能な代替接続を先に試し、代替がない場合にこの待機を適用する（2026年11月4日、resume時に上限中の方式をスキップするユーザー指示）。
  待機は回数と総時間で打ち切らない。
  実装契約は[design-agents-runtime-usage-wait.md](design-agents-runtime-usage-wait.md)にあり、委譲元の扱いは`agent-toolkit/skills/delegation/references/waiting-stalls.md`「停滞の検知と巻き取り」が定める

- Claude・Codexの接続は、サブスクリプション（`subscription`）、有限のAPIクレジット（`credits`）、従量課金API（`payg`）の3種別とする。
  認証の所在は種別ごとの設定キー（Claudeは`claude_credits_env_file`・`claude_payg_env_file`、Codexは`codex_credits_provider`・`codex_payg_provider`）に置く。
  役割（統括担当・委譲先）ごとの使用順は、エンジン別の4キーに置く（2026年10月29日、ユーザー回答「役割別×エンジン別」）。
  4キーは`claude_orchestrator_connections`・`claude_worker_connections`・`codex_orchestrator_connections`・`codex_worker_connections`である。
  組み合わせで意味が決まっていた旧キーは自動移行して撤去する（同日、ユーザー回答「自動移行して撤去」）。
  旧キーは`claude_api_env_file`・`claude_orchestrator_api_only`・`claude_worker_api_first`・`claude_api_mode`・`codex_model_providers`である。
  従量課金は使用順へ`payg`を明記した役割だけで使い、既定の使用順（統括担当`subscription,credits`、委譲先`credits,subscription`）に含めない（同日、ユーザー回答「明示したときだけ使う」。追加料金が発生するため）。
  委譲先の既定値は、失効する前払いのクレジットを先に使い、サブスクリプションを次に使う順とする。
  使用順は先頭から評価し、認証が未設定の種別は除く。
  使える接続が1〜2種しかない環境でも同じ使用順のまま設定済みの接続だけを使うためである。
  エンジンごとに認証キー2つと使用順2つのどれも設定しない場合は、親環境と通常の認証解決を引き継ぐ（Claudeは`inherit`接続でAPIキーやBedrock・Vertexなども使え、Codexは`config.toml`の通常設定に従い代替試行しない）。
  接続を管理しない環境で開始した`inherit`の会話と、接続情報もAPIパスも持たない旧形式の会話は、接続を管理しない間は再開時も通常の認証解決を維持する。
  接続を管理し始めた後は、これらの会話も使用順の種別ではないため、次の継続で使用順の先頭の利用可能な候補へ移す（旧`api-only`と統括担当の接続固定が`inherit`の会話を移していた挙動による）。
  Codexのprovider定義と認証は`config.toml`が持ち、新キーは種別とprovider IDの対応だけを持つ。
  主接続先と移行先の選択設定を2カ所へ分散させない2026年10月10日のユーザー回答の目的は、選択順を役割ごとの使用順1キーに置くことで保つ。
  Codexの`subscription`はprovider `openai`をChatGPTログインで使う場合だけを指し、APIキーのアカウントでは候補から除いて`codex_payg_provider`か`codex_credits_provider`への`openai`の設定を案内する。
  従量課金が明記した役割だけで使われることを、アカウント種別によらず保つためである。
  APIクレジットはClaude APIとAgent SDKを対象とし、Claude Codeを対象外とするため、`claude` CLIで起動するClaudeの統括担当（process-loop）は`credits`を除く。
  委譲先は、`subscription`の待機対象外の利用上限、`credits`の残高不足、`payg`の不受理で、同じ会話を使用順の次の候補へ移す。
  Codexの統括担当は起動時に先頭の利用可能な候補を選び、会話の中で切り替えない。
  Claude統括はprocess-loop・run-skillの再開時に上限中の接続方式をスキップし、元の会話と配下を復旧する。
  役割別の2キーと現在の設定値は変更しない（2026年11月4日、ユーザー指示）。
  継続・再開する会話は、Claude統括CLIの利用上限時の切替を除き保持した接続を使い、その種別を使用順から外した場合に、次の継続（追送・自動再開・保存後再開）で使用順の先頭の利用可能な候補へ移して戻さない。
  旧`api-only`（2026年10月22日、ユーザー指示。既存会話も次の継続からAPIへ移し、サブスクリプションへ戻さない）と統括担当の接続固定の挙動は、この規則で表す。
  `credits`の残高不足の観測から1時間は、新規の委譲先で`credits`を使用順の後ろへ回し、観測は永続化しない（ユーザー要件。APIクレジットは月1回の付与などで外部から回復するため）。
  両エンジンへ適用する。
  Claudeは利用上限で`subscription`から移した委譲先を、使用順で`subscription`が現在の種別より前にあれば、上限の解除予定時刻の後に始まる最初のturnで同じ会話を`subscription`へ戻す（2026年10月18日、ユーザー指示「すぐAPIに切り替えつつ上限解除されたらまたサブスク優先に戻る動作がいい」）。
  戻す時刻は所有MCPサーバーのメモリーだけに置き、解放後の再開は保持した接続を使う。
  Codexは新規の会話だけを使用順の先頭から評価し、移った会話は移行先を維持する（2026年10月9日、ユーザー指示。既存API会話を維持する理由はキャッシュ効率）。
  切替の事実とprovider名を委譲元の応答・通知・公開状態や継続指示へ加えない（同日、ユーザー指示。委譲先の混乱を避けるため）。
  Claudeは親環境と親settingsからAPI認証を隔離し、選択したenvの値は起動ごとに読み直す。
  ユーザーの直接起動、組み込みAgentとfork、run-skill、commitは対象外である。
  エンドユーザー向けの説明と条件ごとの設定例は`docs/guide/claude-code-connections.md`「接続の使い分け」にあり、`atk config --help`からその節のURLを案内する（2026年10月29日、ユーザー指示）。
  実装契約は[design-agents-runtime-connections.md](design-agents-runtime-connections.md#api失敗過負荷利用上限の待機)にある
- 利用上限後の再開は、次の起動形態ごとに扱う。
  解除待ちのagents_serverの委譲先と背景wait・定期再確認は保持する。
  Codexの候補判定は次候補を優先し、全候補失敗かつ上限失敗がある場合だけ解除を待つ

| 起動形態 | 解除後の再開の適用と理由 |
| --- | --- |
| Claudeメイン、Remote Control接続 | Function hooksが解除後の再開を予約する |
| Claudeメイン、Remote Control非接続 | ホストの自動継続を保ち、Function hooksも再開を予約する。解除後に既にturnが始まっていれば重複投入しない |
| Claudeのprocess-loopの子 | Remote Controlを無効にした対話起動で既存のホストの自動継続とgoalの再開を保ち、次の反復の可用性判定も解除を待つ |
| Claudeのrun-skill | スキル実行中の上限でも解除後に同じ会話をresumeする |
| Claudeのcommit | 既存の解除待ちと同じ会話のresumeを保つ |
| agents_serverのClaude委譲先 | 既存の解除待ちと再開を保ち、Function hooksによる再開予約からは除外する。待機中の委譲先を保持する停止判断と、前項のAPI移行設定を適用する |
| Agentツールのサブエージェント | メインのセッションの一部であり、メインの再開で委譲元が続ける |
| Codexのrun-skill | 解除後に`codex exec resume`で同じ会話を再開する |
| Codexのprocess-loopの子 | 上限で止まった所有する子を終了し、次の反復の候補判定を経てレーンとWIから続ける |
| 端末で`codex`を直接起動した対話画面 | 自動再開しない。外部からの`codex queue`は画面を閉じた会話も無人で再開させ、送信側が画面の終了を判別できないため（2026年10月24日の実機観測、[design-agents-runtime-handoff-resume.md](design-agents-runtime-handoff-resume.md)）。解除後に`/goal resume`か発話で続ける |
| Codex TUIの子thread | 親threadの再開で親が続ける |
| agents_serverのCodex委譲先 | run-skillとprocess-loopの再開の対象外。既存の接続先移行と、終了した前任からの引き継ぎ起動で扱う |
| Codexのcommit | `--ephemeral`の単発操作であり、失敗を終了コードと次の操作として呼出元のエージェントへ返す |
