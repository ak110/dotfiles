# 選定とレーン分けの開始

①の開始時に、process-feedbacksのメインが本書を全文読み、選定の前処理、pickerの起動及び①の完了判定へ適用する。
pickerの起動契約、渡す入力と出力の検収は`${CLAUDE_PLUGIN_ROOT}/share/pick-feedbacks.parent.md`を正本とし、本書へ複製しない。

## 計画保存先の移行

メインが`atk plans migrate`を1回実行する。当該コマンドは移行対象の有無にかかわらず、先に計画保存先の作業ツリーがcleanであることを検査してremoteと同期する。
他の主体が取得中の計画バンドルは、所有記録の実在により同コマンドが移行対象から除く。
当該コマンドが非0で終了した場合は再試行せず、終了コードと出力を報告して選定を続行する。

## pickerの起動と出力の検収

`${CLAUDE_PLUGIN_ROOT}/share/pick-feedbacks.parent.md`を全文読み、同書に従ってpickerを起動して出力を検収する。処理対象の決定はpickerが担う。

## ①の完了

選定時に`inbox`だった全項目を`processing`へ移すまでを①の完了条件とする。
`processing`へ遷移していない項目が残る場合の扱いは`${CLAUDE_PLUGIN_ROOT}/share/pick-feedbacks.parent.md`の「処理開始」節が定める。
