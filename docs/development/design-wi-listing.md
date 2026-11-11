# キュー状態と一覧出力の設計記録

[設計記録の索引](design.md)と[WIキューと要求本文の設計記録](design-wi.md)から参照する主題別の記録である。

## WIキューと実行準備

### キュー状態と公開一覧

重要フラグは状態とは独立したfrontmatterの`important: true`で表す。
キーが欠ける項目はfalseとし、解除時はキーを削除する。
共通選択条件とWeb一覧は`all/yes/no`で表示対象を限定する。
通常のprocess-wiの選定・優先度・状態遷移はこの値を参照せず、ユーザーが重要項目を明示した場合だけ対象集合へ反映する。
重要を新しい状態や自動優先度へ変換する案は、明示された選定条件を変えるため採用しない。

キューの全状態は`inbox`、`processing`、`hold`、`adopted`、`rejected`である。
公開一覧の`active`は`inbox`、`processing`、`hold`を表示し、`processable`は通常の自動処理へ渡せる`inbox`と`processing`だけを表示する。
一覧集合は型によらず同じ定義とする。
`hold`は明示操作まで処理ループ、readiness、UWIスキャンおよびalertsの対象にしない。

計画作成中の項目と外部編集中の項目を分けていた`planning`と`editing`は自動処理から除外する点で`hold`と同じ振る舞いであり、状態集合の定義を型と操作ごとに分岐させていたため廃止した。
役割は`hold`へ統合し、状態集合の定義を`agent-toolkit/agent_toolkit/_atk/wi/constants.py`へ集約する。

`hold`は`inbox`、`processing`、`adopted`、`rejected`から移動し、`unhold`で`inbox`へ戻す。
保留元の状態を推測して`processing`へ戻す操作は設けない。
終了状態から保留または受信へ戻す際には旧`## 処理結果`を除き、前回の採否を再開後の結果として残さない。
`hold`以外の`unhold`は拒否する。
`hold`は自動処理からの除外だけを意味するため、保留中の編集、UWI回答、ユーザーコメント、採否および削除は`inbox`と同じ条件で許可する。
CLIのファイル名指定による`adopt`・`reject`は`processing`、`inbox`、`hold`の順で対象を解決し、探索する状態の集合を`--all`の候補と同じ`BULK_SOURCE_STATES`から導く。
両方の処理が状態を別々に列挙すると、状態の追加が片方にだけ反映されるためである。
削除は終了状態（`adopted`・`rejected`）も明示`state`として受理し、`processing`の削除保護だけを維持する。

エージェントが実行できる遷移は、`inbox`と`hold`の相互移動、`inbox`から`processing`を経た`adopted`・`rejected`への終了、`hold`から`adopted`・`rejected`への直接の終了、および両終了状態から`inbox`または`hold`への差し戻しとする。
ユーザーはブラウザーUIから同じ再開・保留と終了項目の削除を行える。
誤終了を削除と再投入だけで復旧する案は、元項目の由来と本文を保てないため採用しない。

### 一覧出力

`atk wi list`は`AI_AGENT`、`CODEX_CI`、`CLAUDECODE`、`CURSOR_AGENT`のいずれかが設定されたエージェント環境ではオプション指定が無い場合にJSON Linesを出力する。
`--jsonl`はJSON Lines、`--no-jsonl`は従来のテキスト表示、`--count`は件数だけの表示として優先する。
旧名の`--json`は単一のJSON配列と誤読されたため`--jsonl`へ改名し、旧名が新名の省略形として受理され続けないよう`list`だけ省略形の解釈を無効にする。
オプション指定が無い場合の出力形式を変えるこの規則は`list`だけに適用し、`show`、状態遷移および編集CLIの既存テキスト出力は変更しない。

テキスト表示の`target_repo`と要約はstdoutがTTYである場合だけ端末幅に応じて短縮する。
パイプやリダイレクトなど非TTYのテキスト表示では全文を保持し、機械取得で本文の手掛かりを失わせない。
人間がTTYで表示する既存の幅適応は維持する。
