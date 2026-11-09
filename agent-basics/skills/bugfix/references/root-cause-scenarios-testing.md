# 場面別の原因分析：テストの原因調査

## 名指し参照の読込方法

名指し参照は`agent-basics:writing-standards/references/read-method.md`に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文の条件に従い「前提の再検討」を判断する前 | `root-cause-severity-quality.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-severity-quality.md` |
| 本文の条件に従い「再発防止策の必須性」を判断する前 | `root-cause-prevention-requirements.md` | 判断 | `read:agent-basics:*/skills/bugfix/references/root-cause-prevention-requirements.md` |
| 本文の条件に従い参照先の基準を使う前 | `../../writing-standards/references/testing.md` | 判断 | `read:agent-basics:*/skills/writing-standards/references/testing.md` |

## テストの原因調査

テストが失敗した場合も通常の不具合と同様に共通の初動手順に沿って対処し、テスト対象の機能側に問題があるのか、それともテストの設計や実行条件側に問題があるのかを切り分けて原因を特定する。
実行ごとに結果が変わる不安定な失敗（フレイキーテスト）については、待機処理が期待している状態にまだ到達していないことが原因なのか、あるいは共有された外部環境へ同時にアクセスする別のプロセスなどが干渉していることが原因なのかを、実際のログや挙動から区別した上で修正方法を選択する。
同期処理、環境の隔離、決定論的なテスト入力の設計、タイムアウト上限の設定、およびトレースログの取得に関する技術的な基準は、`agent-basics:writing-standards`の`references/testing.md`および対象技術の公式ドキュメントに従う。

## 所要の原因調査

実行ごとの個別または全体の所要時間の比較方法は、`../../check-execution/references/diagnostics-results.md`「読む出力」に従う。
そこで確認された、理由の分からない所要時間の増大や不要な処理については、事前準備、待機、外部通信やデータ取得、プロセスの起動といった区間ごとに条件を揃えて測定・比較し、原因を特定して改善策を選択する。
すでに出力されているログなどから判断できる内容については再測定せず、原因を切り分けるための情報が不足している疑わしい区間に限って追加の測定を実施する。
