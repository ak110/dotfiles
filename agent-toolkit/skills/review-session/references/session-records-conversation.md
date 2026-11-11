# セッション記録の構造と集計：会話の少量取得

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

## 会話の少量取得

復元、進捗点検、状況照会、引き継ぎ、操作の調査では、`session-review-prepare --conversation-only`の抽出を共用する。
会話圧縮後の復元では、ユーザー発話の原文、作業契約、計画と進捗、引き継ぎ記録を先に読み、進行中の操作は直近の少量表示で補う。
全期間の通常表示は、それでも目的や経緯を復元できない場合と、担当の交代で後任が全体を把握する場合に読む。
前回以降の点検でも少量表示を選ぶ。
通常表示は予算を超える会話でも全期間から行を選んで省略区間を示すため、全体の把握のための少量表示の継続取得は省く。
対象記録の指定は、同じコマンドの`--transcript`、`--codex-thread-id`、`--opencode-session-id`から選ぶ。

```sh
atk run-script session-review-prepare -- --transcript <記録の絶対パス> --conversation-only --conversation-window --conversation-recent 30
```

少量表示の条件は次のとおりであり、公開ヘルプで受理形式を確認して組み合わせる。

- `--conversation-record <記録ID>`: 表示する担当記録を選ぶ。
  複数指定できる。
- `--conversation-since <ISO8601>`・`--conversation-until <ISO8601>`: timezone付き日時で期間を選ぶ。
  時刻不明の範囲は期間内の成功や0件の対象から除く。
- `--conversation-recent <正整数>`: 選択範囲の既知時刻から直近件数を選ぶ。
  時刻不明の除外は件数を確認し、直近順を確定したものと扱わない。
- `--conversation-after <非負整数>`: 表示が返した位置より後を取得する。
  既存の`--after <scope JSON>`が定める振り返り対象の差分とは区別する。
- `--conversation-max-bytes <正整数>`: 少量文書全体のUTF-8予算を指定する。
  省略時の16000バイトは、日常点検で読む量を全体概要より小さくするための値である。

生成された`conversation_path`と、`conversation_window`が示す実バイト数、条件、対象・表示件数、省略と未確認範囲、継続案内を確認する。
継続位置は同じ抽出範囲で使い、対象記録を切り替える際はその記録の位置から取得する。
対象記録へ追記された次回の点検では、`--after <前回のbundle/scope.json>`を付けて新しく抽出する。
`--conversation-after`は同じ保存bundleのページ継続に使い、追記の再抽出とは分ける。
省略した本文・入力・結果は表示中の記録位置を`session-review-evidence --detail <記録位置>`へ渡して取得する。
人間の要求・判断・認可は`--user-events`と直前の提案でも読み比べ、抜粋だけから確定しない。
生成済みbundleは全文証拠として保持し、毎回の全履歴抽出と読み直しは点検の条件から除く。

途中の発話はその時点の観測として扱い、最後の発話の自動的な完了報告扱いは対象外とする。
現在の進捗と終了は実行識別子の公開状態照会と併用する。
記録が不足するときは未確認と示し、詳細取得または担当への照会で補う。
