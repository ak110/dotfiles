# 運用機能: 週次の費用指標

## 週次の費用指標（cost-metrics）の出力

`atk run-script cost-metrics -- --since <開始> [--until <終了>]`はJSON Linesで次の行を返す。
日付と時差のない日時は実行ホストのローカル時刻として解釈する。
母集団と集計方式の設計は[design-session-review.md](design-session-review.md)の「手動の週次費用指標」にある。

`cost-metrics-week`は月曜0時から始まる週ごとに次の値を返す。

| 項目 | 定義 |
| --- | --- |
| `adopted_awi_count` | private-notesのGit履歴で期間内に採用状態へ変わったAWI数。初出から採用済みの項目も含む。状態を保つ配置移行・本文編集とUWIを除き、全対象リポジトリを合算して同名ファイルを週内で1件とする |
| `tokens` | 開始時刻が期間内のルートと全委譲先のトークン。全ルートとprocess-wiを分け、それぞれ合計とruntime別に、非キャッシュ入力・キャッシュ作成入力・キャッシュ読取入力・出力の4成分を示す。Codexの`reasoning_output_tokens`は出力に内包される |
| `tokens_per_awi` | 全ルートとprocess-wiそれぞれの4成分を同じ週の採用AWI数で割った値。0件の週はnull |
| `review.rounds`・`five_or_more_ratio` | `plans/`配下の保存済み`exec-review.tsv`を最初の追加commitの週へ割り当て、exec-review行の最大roundが0・1・2・3・4・5・6以上の表数と、5以上の表の割合を示す。指摘なしは0。旧trackのimplementation-reviewはexec-reviewとして読み、他のtrackは対象外とする |
| `review.levels` | exec-reviewの指摘を要件・仕様・詳細・実装・旧形式の未記録に分けた件数 |
| `review.without_modification_count`・`without_modification_ratio` | exec-review行のうち`no-response-reason`がありresponseが空の件数と、全exec-review指摘数に対する割合 |
| `unsaved_table_count` | `~/.claude/plans`だけにある未保存の表の件数。集計からは除く |
| `missing_token_records` | 最古の読めた記録より前に始まる週ではtrueとなる。この週の0は使用量0の証拠にならない |

各記録は1回だけ数え、委譲先はルートの開始週へ合算する。
ルート候補は更新時刻が開始より前なら読込前に除き、選んだルートの子孫は更新時刻で除外しない。

その他の行は次のとおりである。

- `cost-metrics-session`: 期間内に開始したルートごとのengine・session_id・開始時刻・cwdと、子孫を含む推定費用（USD）。
  `estimated_usd`は単価不明や記録不足があるとnullになり、`known_usd`が算出できた部分、`unknown_responses`と`unknown_tokens`が単価不明の応答数と使用量を示す。
  `breakdown.self`はその記録のClaude累積額、`claude_subagents`はそれに含まれない追加分、`delegates`は別プロセスの委譲先の追加分である。
  Claudeの最新の算出額を優先し、残りはモデルごとのトークン単価で補う。
  料金表のURL・基準日・算出式とTTL不明時の仮定は末尾の`pricing`に含まれる
- `cost-metrics-hook-notice`: ルートの開始週・runtime・規則（`hook`、`hook_name`、`tag`、`kind_text`）ごとのhook通知の`count`。
  ツール結果だけにある遮断も数え、同一呼び出しの重複と通常の標準出力・会話中の引用を除く
- `cost-metrics-coverage`: runtimeごとの読めた最古の記録時刻、読めなかった記録・表、特定できなかった委譲先の件数

`--claude-projects-dir`・`--codex-sessions-dir`・`--private-notes`で入力のディレクトリを上書きできる。
指定を省くと`~/.claude/projects`・`~/.codex/sessions`・`atk config get private_notes`の値を使う。
