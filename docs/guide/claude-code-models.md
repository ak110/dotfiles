# 設定確認と工程別モデル設定

各キーの既定値と書式は、`atk serve`の「設定」から確認できる。

## 設定確認

初回の確認コマンドと期待結果は[READMEの導入確認](../../agent-toolkit/README.md#導入確認)を参照。

委譲先（エージェントが作業を任せた別のエージェント）の状態は次のコマンドで確認する。

- `atk agents list`: 実行中と保持中の委譲先を一覧する。
  `--watch`で約2秒ごとに更新する
- `atk agents logs <session-id>`: 委譲先の会話を要約して表示する。
  `--follow`で追記分を表示し続け、`--verbose`で全イベントを表示する

### 費用とレビュー指標の集計

会話中の表示を拡張するModsの始め方は[導入ガイド](claude-code-guide.md#modsでclaude-codeを拡張する)を参照。
Modsの画面表示と、ここで説明する保存記録からの費用集計は別の機能である。

期間を指定して、トークン使用量、API料金換算の推定費用、レビューの反復回数を週ごとに集計できる。
終了を省くと実行時刻までを集計する。

```sh
atk run-script cost-metrics -- --since 2026-10-05 --until 2026-10-19
```

出力はJSON Linesで、費用は実際の支払額とは異なる推定値である。
記録が欠けた週の0は使用量0を意味しない。
各出力項目の定義は[operations-cost-metrics.md](../development/operations-cost-metrics.md#週次の費用指標cost-metricsの出力)を参照。

## 工程別モデル設定

### 利用枠とreset許可

利用枠は画面の裏で取り直す。
前回取得できた値は表示に残り、取得できない状態が続いたら「再取得」で取り直せる。

`atk config usage`はClaudeとCodexの残量・回復時刻・最終観測、resetの在庫と案内を表示する。
`--refresh`で利用枠を取り直し、`--json`で詳細を表示する。
取得できない枠は不明とし、取得済みの観測は時刻とともに表示する。
追加課金の使用額、サブスクリプションの使用率、トークンから推定したAPI料金は別の値である。

resetの自動消費は`usage_reset_permissions`の許可だけを使う。
`atk serve`の「利用枠」では、使用率とリセット権を確認できる。
画面で許可を登録するには「リセットの許可を設定」でエンジン・アカウントを選び、回数と開始・終了を確認して追加する。
終了は週次リセットの1〜3日前を選べる。
日時はブラウザーの時刻で入力する。
おすすめは入力を補うだけで、保存は自動で行わない。
CLIで許可を書く場合、Codexの`account`は`atk config usage --json`の`metadata.codex.permission_account`を使う。
空なら`atk config usage --refresh`で取り直すか、メールアドレスまたはそのSHA-256を使う。
Claudeでは登録済みの在庫の`account`を使う。
`account`は確認したアカウント、日時はタイムゾーン付き、`count`は期間内の上限回数とする。
Codexの対象は`scope`を`codex`とし、旧`codex_auto_reset=true`だけを消費許可には読み替えない。
自然回復が近い場合や作業がない場合は消費しない。
検収や導入だけを目的に実際の権利を消費しない。
消費ペースから週次枠の枯渇を見込む時は、同じ観測元・契約・アカウントで2区間かつ30分以上の稼働を観測し、直近の観測が10分以内なら、その稼働割合を反映する。
観測が足りなければ連続稼働を仮定する。
許可が空なら、自動resetのための定期照会は行わない。

Claudeの在庫・期限・対象は`atk config usage --set-claude-reset-inventory <JSON配列>`に入力し、`atk config usage --json`の案内にある公式Usage画面で手動消費する。
消費後は在庫を更新し、`atk config usage --refresh`で残量を取り直す。
Fable限定などの対象を、他モデルや別の枠の回復へ読み替えない。

### モデル候補の保存

エージェントが作業の工程ごとに使うモデルを`atk config`で設定する。
各キーには候補をカンマ区切りで並べ、保存した順に試す。
起動できない場合は次の候補へ進む。

キーの用途・初期値・形式は「設定」の「工程別モデル」で確認する。
OpenCodeの導入とprovider定義は[README](dotfiles-settings.md#opencodeの委譲先)を参照。

`agy`を委譲先に使うには、対応版を導入し、`~/.gemini/antigravity-cli/settings.json`で`"modelProvider": "gemini"`を選び、起動元に`GEMINI_API_KEY`を設定する。
設定またはキーがない場合は個人ログインで起動せず、次の候補へ進む。
再開・追送でも同じ条件を確認する。
条件の根拠は[AntigravityのFAQ](https://antigravity.google/docs/faq)と[CLI認証の説明](https://antigravity.google/docs/install?tab=cli)を2026年11月22日に確認した。
キーを引数やログに入れない。

```bash
atk config show
atk config set review_model agy:gemini-4-argon/medium
atk config set chores_model opencode:local/Qwen/Qwen3.8-27B@thinking,claude:sonnet/medium
```

`atk config show`は通信や設定の移行をせず、現在値を一覧する。
旧設定からも読み取りだけで移行後の値を求め、確定できない項目だけを省いて原因と次の操作を示す。
`atk config get <キー>`は値を返す前に、旧キーの置換と保存や旧`serve.toml`の改名など、必要な設定ファイルを移行する。
端末で `codex_fast_mode` が `auto` の場合、`atk config show` はこのホストでの最後の委譲起動時の速度、時刻、理由、判定の出所を標準エラーに表示する。
`atk serve` の設定画面でも、選んだホストの起動時判定を現在の設定値と分けて表示する。
記録がなければその旨を表示する。
自動判定は契約の取得に失敗すると再試行し、同じワークスペースと接続を確認できる短時間に限って直前の成功判定を使う。
`AGENT_TOOLKIT_CONFIG_<キー名の大文字>`の環境変数を設定すると保存値より優先する。
保存済みのモデル候補は初期値を変えても書き換えない。
以前の設定キー`usage_auto_allocation`は`atk config get`での移行時に削除する。
環境変数で指定している場合は解除する。

一時指定は通常設定に1つの上書きを重ねる。
次は書式例であり、モデルIDは使用するホストが受理するものを指定する。

```sh
atk config temporary set execute_model 'codex:MODEL_ID/high,claude:MODEL_ID/low' --until 2027-01-01T00:00:00+09:00
atk config temporary show
atk config temporary clear
```

期限を省略すると明示解除まで有効になる。
期限は上書き全体へ適用し、指定のないキーは通常設定を使う。
期限後はその時点の最新の通常設定へ戻り、一時指定前の値を書き戻さない。
環境変数による明示指定は一時指定より優先する。
特定モデルだけを使う場合は候補を1つにする。
実行中turnは強制終了せず、新規起動・継続の境界で適用する。

通常の委譲先は、同じ会話の次turnからeffortを1段ずつ調整できる。

```sh
atk agents effort up
atk agents effort down <子のsession_id>
```

対象IDの省略時は自身、指定時は自身または直接の子を対象にする。
自身を変更した後は現在turnの終了後に1回再開する。
実行中turnは中断せず、モデルと保存設定を維持する。
orchestrate、対話中のメインと軽量起動は対象外である。

モデルと推論強度（effort）は、同程度の作業が完成するまでの再試行・修正・委譲も含めて比較する。
1回のトークン量や時間当たりの利用枠消費だけでは、完成までの費用は分からない。
まず既存の工程別設定で役割に合うモデルを選び、低いeffortと高いeffortのどちらも無条件に省費用とは考えない。

推論量の不足を示す具体的な根拠があるときは、次の順に試す。
早期返却・停滞・入力不足には原因を直して同じeffortで続ける。

1. 委譲先が自分の成果を確かめられる手段（テスト、ビルドなど）が作業に用意されているかを確かめる。
2. `atk agents effort up <子のsession_id>`で同じ会話のeffortを1段上げる。
3. 上のモデルへ移る。
   移った先では、下のモデルで使っていた値より低いeffortから始める。

`high`を超えるeffortへ上げる前に、1つ上のモデルを低いeffortで試して比べる。
順番の出典は[Which Claude model should you build with?](https://claude.dev/blog/which-claude-model-should-you-build-with)である。

Claude Codeの[advisor](https://code.claude.com/docs/en/advisor)は、全会話を読む別モデルへの追加相談で、呼び出すたびにそのモデルの使用量が加わる。
[Ultracode](https://code.claude.com/docs/en/model-config#adjust-effort-level)は動的なワークフローを構成する設定で、effortとは別の機能である。
追加する場合は、既存の委譲・独立文脈レビューと役割が重ならないか、相談や作業構成にかかる費用を含めて比較する。
独立した文脈で成果物を確かめるレビューは維持し、advisorやUltracodeの常時有効化を前提にしない。

agent-toolkitには、advisorとは別に、委譲先が自分を起動した委譲元へ助言を求める仕組みがある。
委譲元が別エンジンのモデルか、同じエンジンで上位のモデルである場合に、委譲先は自力で進められなくなった時点で助言を依頼できる。
委譲元は委譲先の会話の抜粋（省略時は16,000バイトまで）だけを読んで助言を返すため、会話全体を読むadvisorより追加の使用量が小さく、Codexなどの委譲先でも使える。
設定は不要で、対象になる組み合わせでは自動で有効になる。

自由本文で割り当て済みの実装・修正を委譲する場合は、`start(mode=delegate, model_type=execute)` が `execute_model` を使う。
レーンの実装は所定の `exec` の役割で起動する。
`write_model` はユーザーが指定した場合だけ使い、起動時には `model_override=user_instruction` と `model_override_evidence` に実際の指示の根拠を渡す。
後任を起動する場合も同じ指定が必要になる。

`atk config apply-preset <名前>`は`write_model`を除く6工程を一括で設定する。
引数を省くか`show`を指定すると、各プリセットの内容を保存せずに表示する。

| プリセット | 初期値から変更する工程 |
| --- | --- |
| `codex-balanced` | executeを`claude:opus[1m]/medium,codex:sol/medium`へ変更 |
| `codex-near-primary` | 初期値と同一 |
| `codex-primary` | orchestrateを`codex:sol/medium,claude:opus[1m]/medium`へ変更 |
| `claude-balanced` | executeを`claude:opus[1m]/medium,codex:sol/medium`、choresを`claude:sonnet[1m]/low,codex:sol/low`、chores_fastを`claude:haiku/low,codex:luna/low`へ変更 |
| `claude-primary` | executeを`claude:opus[1m]/medium,codex:sol/medium`、reviewを`claude:sonnet[1m]/medium,codex:sol/medium`、review_fastを`claude:haiku/medium,codex:luna/medium`、choresを`claude:sonnet[1m]/low,codex:sol/low`、chores_fastを`claude:haiku/low,codex:luna/low`へ変更 |

その他の主な設定は次のとおりである。

| キー | 内容 |
| --- | --- |
| `codex_fast_mode` | 初期値は`auto`。対象契約のサブスクリプション接続で、追加料金なしの提供期間中だけfast modeを使う。`true`は常に有効、`false`は常に無効 |
| `claude_usage_limit_wait`・`codex_usage_limit_wait` | 利用上限に達した時に解除を待つ範囲。`five-hour`（初期値）は5時間枠だけ、`always`は週次枠なども待ち、`never`は待たずに次の候補へ進む |
| `claude_*_connections`・`codex_*_connections`ほか | Claude・Codexのサブスクリプション・APIクレジット・従量課金APIの使い分け。設定は[接続の使い分け](claude-code-connections.md#接続の使い分け)を参照 |

未設定の環境では、executeを`codex:sol/medium,claude:opus[1m]/medium`の順に試す。
以前に保存した候補列はそのまま使う。

同じprivate-notesを同期する環境では、工程別モデル、`codex_fast_mode`、`usage_reset_permissions`を共有する。
reset許可の回数は、共有先の予約台帳で全端末の合計として数える。
ファイルの場所と共有設定から解決したキーは`atk config show`の標準エラーで確認できる。
特定のホストだけ変える場合は`atk config temporary`か`AGENT_TOOLKIT_CONFIG_<KEY>`を使う。
共有するキーのローカル値が残っている場合は、[共有設定への移行](claude-code-settings.md#共有設定への移行)を先に行う。
`set`で保存済みでも上書き元の案内が出たときは、その設定を解除して`get`で確かめる。

初期設定ではCodexの5時間枠の解除を待つ。
それ以外の枠は適用できるreset許可があれば先に確認し、resetしなければ使用順の残りの接続先や次のモデル候補を使う。
利用上限で待つ間は、`agents_server`の委譲先、`atk wi process-loop`、`atk run-skill`が解除後に同じ会話で作業を続け、待機理由と予定時刻を表示する。
ユーザーが起動したClaude Codeの会話も、agent-toolkitのFunction hooksを有効にしていれば解除後に自動で再開する。
解除予定が不明な場合は300秒ごとに再確認するため、待機中もClaude Codeを起動したままにする。
[Remote Control](https://code.claude.com/docs/en/remote-control)で接続している間も同じく再開する。
