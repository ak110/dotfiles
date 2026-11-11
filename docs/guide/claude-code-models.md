# 設定確認と工程別モデル設定

## 設定確認

初回の確認コマンドと期待結果は[READMEの導入確認](../../agent-toolkit/README.md#導入確認)を参照。

委譲先（エージェントが作業を任せた別のエージェント）の状態は次のコマンドで確認する。

- `atk agents list`: 実行中と保持中の委譲先を一覧する。
  `--watch`で約2秒ごとに更新する
- `atk agents logs <session-id>`: 委譲先の会話を要約して表示する。
  `--follow`で追記分を表示し続け、`--verbose`で全イベントを表示する

### 費用とレビュー指標の集計

期間を指定して、トークン使用量、API料金換算の推定費用、レビューの反復回数を週ごとに集計できる。
終了を省くと実行時刻までを集計する。

```sh
atk run-script cost-metrics -- --since 2026-10-05 --until 2026-10-19
```

出力はJSON Linesで、費用は実際の支払額とは異なる推定値である。
記録が欠けた週の0は使用量0を意味しない。
各出力項目の定義は[operations-cost-metrics.md](../development/operations-cost-metrics.md#週次の費用指標cost-metricsの出力)を参照。

## 工程別モデル設定

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

成果が足りない委譲の推論量やモデルを調整するときは、次の順に試す。

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
