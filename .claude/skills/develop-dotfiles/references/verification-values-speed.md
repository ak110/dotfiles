# dotfilesの検証で使う値：テストとCIの所要基準

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| テストとCIの所要基準を使う前 | `../../../../agent-basics/skills/writing-standards/references/testing-speed.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/testing-speed.md` |
| テストとCIの所要基準を使う前 | `agent-basics:check-execution/references/diagnostics-results.md` | 判断 | `read:agent-basics:*/skills/check-execution/references/diagnostics-results.md` |
| 共有実行枠のコマンドをシェルから起動する前 | `agent-toolkit:/share/check-execution-operations-launch.md` | 判断 | `read:agent-toolkit:*/share/check-execution-operations-launch.md` |

## テストとCIの所要基準

執筆者は追加・変更するテストを次の段階へ分類し、レビュー担当はその段階の必要性と削減余地、結果確認担当は実測を確認する。
共通の評価方法は次の資料に従う。

- `../../../../agent-basics/skills/writing-standards/references/testing-speed.md`「速度」
- `agent-basics:check-execution/references/diagnostics-results.md`「読む出力」

| 必要な仕事 | テスト本体の改善判断値 |
| --- | --- |
| 同一プロセス内の通常処理 | 0.1秒 |
| 契約上不可避の実時間待機を含む処理 | 0.5秒 |
| 実プロセス・実Gitなどの境界を使う処理 | 2秒 |
| 外部接続・ブラウザー・大規模結合など | 固有の検出契約、最小構成、起動・待機回数に基づく個別の所要見込み |

通常処理と実境界の値は、2026年10月31日の選択標本88ケースで本体87ケースが0.1秒以下、実プロセス1ケースが0.73秒だった実績を出発点とする。
待機の値は必要待機量0.08秒の共有ロック試験が本体0.11秒だった実測に余裕を持たせた値である。
これらは選択標本に基づく改善判断値とし、全テストの分布から推定する上限と区別する。
setupと後始末も別に読み、全体への寄与を評価する。

0.1秒を超える構成は必要最小限とし、上位段階には、その層でしか検出できない契約違反と代表シナリオを残す。
待機の段階は契約上必要な待機量から選び、固定sleepの長さだけによる段階の引き上げは禁止する。
基準超過時には、軽い層への移動、不変準備の共有、データ量と起動・待機回数の削減を検討する。
OS・負荷・準備状態に由来する差と必要仕事による超過理由、最小化の判断を既存の検証記録に残す。
固定秒数だけでテストを失敗させる機械チェックの追加は禁止する。

CI workflowの全体基準は、対象HEADの実行開始から必須ジョブ終了までの実時間10分とする。
以前の同workflowの成功実績8〜9分へ約1〜2分の余裕を持たせた改善判断値であり、キュー待ち・人間の承認待ちは分ける。
公開担当とPR検収担当は成功時も比較し、律速ジョブの準備・本体・終了処理を内訳として既存の検証記録へ残す。
10分超なら原因を調べ、現在の範囲と認可で成立する改善を同じ作業で行う。
外部前提や別計画が必要な残作業だけ、原因、具体策、解除条件を付けて引き継ぐ。
公開工程と必要な検証は、所要超過時も継続する。
同条件比較では必須テスト集合、並列度、依存版、コンテナーと環境隔離の一致を確かめる。
この固有数値の適用範囲はdotfilesに限る。
