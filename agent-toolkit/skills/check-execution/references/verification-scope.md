# 検証対象の品質基準と記録操作

品質の定義元は`agent-basics:check-execution`の`references/verification-scope.md`である。呼出元が指定した条件と読込範囲を、同名の正式な品質資料へ引き継ぐ。節指定がある場合は移設先の実見出しへ接続し、指定がない場合は従来の全文読込契約を保つ。取得済みの同版本文は再取得せず使う。

検証対象の選定では`agent-toolkit:wi-standards`の`references/awi-body.md`「完成条件」の自己観測と未確認範囲の扱い、公開前の全体検証では`agent-toolkit:commit`の`references/publish.md`「検証とCI」を適用する。

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

<!-- read-table-source: verification-scope-delivery-1.md -->

## 実行のしかた

- 計画で確定した検証行は本スキルの`SKILL.md`「統合実行ツール経由の起動」に従って各コマンドを実行し、コマンドごとの終了コードと出力の要点を受領する。凍結後の訂正済み検証表を引き継いだ場合は、その表のコマンドを実行する。訂正表の書式・保持・引き継ぎは`agent-toolkit:plan-mode`の`references/plan-file-standards.md`「検証と終端工程」に従う
- 保存済みの結果は子を再実行せずに読む。保存した両出力は`sed -n`かReadで、JSONは`jq`で読み、取得時のHEAD・cwd・終了コードを同じ記録から確かめる。読取失敗・欠損・終了コードの未記録を成功と扱わない
- 先行結果と限定再実行を合わせるときは、品質の定義元の「実行条件と再実行」で各成功集合の適用可否を判定し、完全テスト名・取得版・合否を要求へ対応付ける
- 試験結果を完成条件へ対応付けるときは、pytestは`-v`、vitest・Playwright・dotnet testはテスト名と結果を1件ずつ出力する報告形式で実行し、その出力のテスト名と結果の行を使う。診断はpyfltrの`show-run --commands <コマンド名> --output-format=jsonl`の保存結果から読む。点表示と総数だけで個別の合否を判定しない

## 直接影響範囲の類型

品質の定義は`agent-basics:check-execution`の`references/verification-scope.md`「直接影響範囲の類型」を適用する。

## 範囲の決め方

品質の定義は`agent-basics:check-execution`の`references/verification-scope.md`「範囲の決め方」を適用する。
