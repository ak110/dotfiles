# dotfilesのprocess-wiの所要時間と終了条件

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 |
| --- | --- |
| `agent-toolkit:process-wi`の所要時間目標を適用する前 | `agent-toolkit:process-wi/SKILL.md` |
| 進捗と時間目標を見直す前 | `agent-basics:writing-standards/references/quality-principles-qcd.md` |

## 所要時間と終了条件

本リポジトリを対象に含む巡回は、選定から公開・CI・後始末・報告までを原則180分以内に収める。
別cwdのall-repos統括にも適用する。
選定時は過去の完走実測から目標に収まる量を決め、`pyproject.toml`の`[tool.agent-toolkit.pick-wi-check]`の`minimum-items`を件数の下限とする。
初期値は21件であり、候補が少なければ全件を選ぶ。
回答済みUWI、AWIの順に、各種別内のファイル名昇順で先頭から連続して選ぶ。
先頭の長い項目や依存の連結成分も含める。
長い先頭項目を選ぶ場合も下限まで後続を選び、独立した仕事を並行処理する。
着手済みと再開項目は保持する。
選定規則は`agent-toolkit:process-wi/references/selection-procedure-allocation.md`、実測の補正は同referencesの`selection-format-prediction.md`に従う。

選んだ全件の実装・必要な品質確認・公開・報告を終えたら巡回を終了する。
着手後は選んだ全件と必要な品質工程を保持し、同じ巡回で完遂する。
目標を超えた回は、原因と改善をAWIとして登録して次の巡回で処理する。
選定時の見込みと実際の全工程の所要、選択件数・残数・下限の適用と根拠にした実測を既存の完了報告へ記録し、次回の選定量に反映する。
次回の目標達成に必要な新たな改善の実装・公開・見込み検収は、次回の選定と実行で行う。
選定確定後の超過は警告にとどめ、選定を受理して配分を比較する。

レーン数の上限は選んだ件数に対する既存の式を保つ。
独立準備・先行レビュー・共有検証と成功再利用により、選んだ仕事を効率よく処理する。
内部担当の読取・実装・レビューを合わせた同時数Cは資源・サービス・総費用から選ぶ。
マシン全体で作業中の委譲先の数は、役割を問わずagents_serverの上限（`atk config`の`agents_concurrency`。初期値は論理CPU数×2.5）で起動時に制限されるため、レーン担当と評価用担当を含めた合計がこの上限を超えるCを見込みに使わない。
進行中の30分点検では、残量・実測速度・待ち・依存を比較し、上限内で構成を見直す。
計測範囲は[セッションの所要時間目標](../../../../.chezmoi-source/dot_claude/docs/session-review-dotfiles.md#セッションの所要時間目標)に従う。
