# 設定確認と工程別モデル設定

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

### 利用枠に応じた自動配分

ClaudeとCodexの残量・回復時刻・観測した消費ペースから、保存済みモデル候補のエンジン順を調整できる。
モデル名とeffort、既存のプリセットは維持する。
dotfilesの通常構成（`full`）では、`update-dotfiles`の後処理が未設定の場合に自動配分を有効にする。
活動モードには実際の稼働・停止の比率を使う`intermittent`を補う。
Linux・Windows共通で適用し、既存の設定値は保持する。
`atk config set usage_auto_allocation false`で無効化した値も、更新時に上書きしない。
agent-toolkitだけを導入した環境では初期状態で無効になっており、次の操作で有効化する。

```sh
atk config usage --refresh
atk config set usage_auto_allocation true
atk config usage
```

確認結果の先頭に優先するエンジンと理由を表示し、続けて利用枠・最終観測・通常設定と一時指定・稼働中の会話を表示する。
取得できない枠は不明として扱い、過去の観測時刻を残す。
追加課金の使用額とサブスクリプションの使用率、トークンから推定したAPI料金は別の値である。
消費ペースは活動時間当たりの使用率の変化で、別端末や並行会話の利用を含み得るため、モデル単独の因果効果とは断定しない。
観測は既存の起動・継続境界とSDK通知を使い、独立した常駐エージェントは起動しない。

仕事など断続的に稼働する環境では`atk config set usage_activity_mode intermittent`を使う。
常時稼働へ戻す場合は`continuous`とする。
私用と仕事の認証・設定・観測値を別環境で管理し、このPCの消費率を仕事環境へコピーしない。
学習前や欠損時は不確かさを示す。
Claudeがひっ迫した場合は統括以外のClaude代替も除き、必要な候補が無ければ設定の不足を示す。
設定していないモデルは補わない。

新モデルの一時指定は、通常設定に1つの上書きを重ねる。
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
エンジン順の自動調整は一時指定した候補にも適用する。
特定モデルだけを使う場合は候補を1つにする。
実行中turnは強制終了せず、新規起動・継続の境界で適用する。
自動配分を無効に戻す操作は`atk config set usage_auto_allocation false`である。

resetの自動消費は別途`usage_reset_permissions`に、`id`、`engine`、`account`、`scope`、`from`、`until`、`count`を持つJSON配列で許可する。
`account`は確認したアカウント、日時はタイムゾーン付き、`count`は期間内の上限回数である。
Codexの対象は`scope`を`codex`とし、旧`codex_auto_reset=true`だけを新方式の消費許可には読み替えない。
自然回復が近い場合や作業がない場合は消費しない。
検収や導入だけを目的に実際の権利を消費しない。
Claudeの在庫・期限・対象は`claude_reset_inventory`に同じ形式で手入力し、確認結果が案内する公式Usage画面で消費する。
消費後は在庫を更新し、`atk config usage --refresh`で残量を取り直す。
Fable限定などの対象を、他モデルや別の枠の回復へ読み替えない。

### モデル候補の保存

エージェントが作業の工程ごとに使うモデルを`atk config`で設定する。
各キーには候補をカンマ区切りで並べ、先頭の候補が使えない場合に次の候補へ進む。

| キー | 用途 | 初期値 |
| --- | --- | --- |
| `execute_model` | 計画・設計判断・原因分析・統合 | `claude:opus[1m]/low,codex:sol/low` |
| `review_model` | 実行レビュー | `codex:sol/medium,claude:sonnet[1m]/medium` |
| `review_fast_model` | 小規模レビュー | `codex:luna/medium,claude:haiku/medium` |
| `chores_model` | 確定済みの作業・中規模の読取 | `codex:sol/low,claude:sonnet[1m]/low` |
| `chores_fast_model` | 定型の走査・軽微な作業・コマンド実行 | `codex:luna/low,claude:haiku/low` |
| `orchestrate_model` | process-loop・run-skillの最上位 | `claude:opus[1m]/medium,codex:sol/medium` |
| `write_model` | 文章起草 | `agy:gemini-3.8-flash/low,claude:claude-opus-5-5/low` |

各候補は`<claude|codex|agy>:<model>[/<effort>]`または`opencode:<provider>/<model>[@<effort>]`で書き、effortを省略するとmediumになる。
`orchestrate_model`はOpenCodeを受け付けない。
OpenCodeの導入とprovider定義は[README](dotfiles-settings.md#opencodeの委譲先)を参照。

```bash
atk config show
atk config set review_model agy:gemini-4-argon/medium
atk config set chores_model opencode:local/Qwen/Qwen3.8-27B@thinking,claude:sonnet/medium
```

`atk config show`は現在値を一覧し、`atk config get <キー>`は指定キーの値を返す。
`AGENT_TOOLKIT_CONFIG_<キー名の大文字>`の環境変数を設定すると保存値より優先する。
保存済みのeffortは初期値の変更では書き換えない。

通常の委譲先は、同じ会話の次turnからeffortを1段ずつ調整できる。

```sh
atk agents effort up
atk agents effort down <子のsession_id>
```

対象IDの省略時は自身、指定時は自身または直接の子を対象にする。
自身を変更した後は現在turnの終了後に1回再開する。
実行中turnは中断せず、モデルと保存設定を維持する。
orchestrate、対話中のメインと軽量起動は対象外である。
規範の累積受信量が目安を超えた場合は、推論量を上げるか判断する案内が1回届く。

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

`atk config apply-preset <名前>`は`write_model`を除く6工程を一括で設定する。
引数を省くか`show`を指定すると、各プリセットの内容を保存せずに表示する。

| プリセット | 初期値から変更する工程 |
| --- | --- |
| `codex-balanced` | 初期値と同一 |
| `codex-near-primary` | executeを`codex:sol/low,claude:opus[1m]/low`へ変更 |
| `codex-primary` | executeを`codex:sol/low,claude:opus[1m]/low`、orchestrateを`codex:sol/medium,claude:opus[1m]/medium`へ変更 |
| `claude-balanced` | choresを`claude:sonnet[1m]/low,codex:sol/low`、chores_fastを`claude:haiku/low,codex:luna/low`へ変更 |
| `claude-primary` | reviewを`claude:sonnet[1m]/medium,codex:sol/medium`、review_fastを`claude:haiku/medium,codex:luna/medium`、choresを`claude:sonnet[1m]/low,codex:sol/low`、chores_fastを`claude:haiku/low,codex:luna/low`へ変更 |

その他の主な設定は次のとおりである。

| キー | 内容 |
| --- | --- |
| `codex_fast_mode` | `true`でCodexのfast modeを使う。初期値は`false` |
| `codex_auto_reset` | `true`でChatGPT接続の週次利用枠を自動resetする。初期値は`false`。条件と設定例は[Codex利用ガイド](codex-guide.md#利用上限後の再開)を参照 |
| `claude_usage_limit_wait`・`codex_usage_limit_wait` | 利用上限に達した時に解除を待つ範囲。`five-hour`（初期値）は5時間枠だけ、`always`は週次枠なども待ち、`never`は待たずに次の候補へ進む |
| `claude_*_connections`・`codex_*_connections`ほか | Claude・Codexのサブスクリプション・APIクレジット・従量課金APIの使い分け。設定は[接続の使い分け](claude-code-connections.md#接続の使い分け)を参照 |

利用上限で待つ間は、`agents_server`の委譲先、`atk wi process-loop`、`atk run-skill`が解除後に同じ会話で作業を続け、待機理由と予定時刻を表示する。
ユーザーが起動したClaude Codeの会話も、agent-toolkitのFunction hooksを有効にしていれば解除後に自動で再開する。
解除予定が不明な場合は300秒ごとに再確認するため、待機中もClaude Codeを起動したままにする。
[Remote Control](https://code.claude.com/docs/en/remote-control)で接続している間も同じく再開する。
