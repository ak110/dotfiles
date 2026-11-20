# 運用機能: 週次の費用指標

## 週次の費用指標（cost-metrics）の出力

`atk run-script cost-metrics -- --since <開始> [--until <終了>]`はJSON Linesで次の行を返す。
日付と時差のない日時は実行ホストのローカル時刻として解釈する。
母集団と集計方式の設計は[design-cost-metrics.md](design-cost-metrics.md)の「手動の週次費用指標」にある。

`cost-metrics-week`は月曜0時から始まる週ごとに次の値を返す。

| 項目 | 定義 |
| --- | --- |
| `adopted_awi_count` | private-notesのGit履歴で期間内に採用状態へ変わったAWI数。初出から採用済みの項目も含む。状態を保つ配置移行・本文編集とUWIを除き、全対象リポジトリを合算して同名ファイルを週内で1件とする |
| `tokens` | 開始時刻が期間内のルートと全委譲先のトークン。全ルートとprocess-wiを分け、それぞれ合計とruntime別に、非キャッシュ入力・キャッシュ作成入力・キャッシュ読取入力・出力の4成分を示す。Codexの`reasoning_output_tokens`は出力に内包される |
| `review.rounds`・`five_or_more_ratio` | `plans/`配下の保存済み`exec-review.tsv`を最初の追加commitの週へ割り当て、最大roundごとの表数と、5以上の表の割合を示す。指摘なしは0。旧trackのimplementation-reviewはexec-reviewとして読み、他のtrackは対象外とする |
| `unsaved_table_count` | `~/.claude/plans`だけにある未保存の表の件数。集計からは除く |
| `missing_token_records` | 最古の読めた記録より前に始まる週ではtrueとなる。この週の0は使用量0の証拠にならない |

他に、ルートごとの推定費用（`cost-metrics-session`）、hook通知の件数（`cost-metrics-hook-notice`）、読めた記録の範囲（`cost-metrics-coverage`）の行を返す。
推定費用（`estimated_usd`）は単価不明や記録不足があるとnullになり、`known_usd`と`unknown_responses`・`unknown_tokens`が算出できた部分と不明の量を示す。
`unknown_models`は単価不明の応答数をモデル名別に示す。
モデル名のない応答のキーは空文字列になる。
hook通知は、ツール結果だけにある遮断も数え、同一呼び出しの重複と通常の標準出力・会話中の引用を除く。
各記録は1回だけ数え、委譲先はルートの開始週へ合算する。
ルート候補は更新時刻が開始より前なら読込前に除き、選んだルートの子孫は更新時刻で除外しない。
