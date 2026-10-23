# Windowsバッチファイル記述スタイル

本書はcmd.exeが実行するバッチファイル（`.cmd`・`.bat`）の記述スタイル基準を定める。

- エンコーディング
  - CP932で記述する。cmd.exeのパーサーはシステムACP（日本語WindowsではCP932）で動作し、`chcp 65001`はコンソールI/Oのコードページしか変えない
  - 読み書きの手段は`encoding.md`「書込ツールの改行・BOM保全」に従う。iconvの文字コード名は`cp932`とする（例: `iconv -f cp932 -t utf-8 file.cmd`）。`git show`もCP932バイト列をそのまま出力するため回避策にならない
- 改行コード
  - CRLFが必須。`.gitattributes`で`*.cmd text eol=crlf`を設定する
- 基本構造
  - コマンドエコーは無効にし、環境変数の作用範囲と終了状態を明確にする
- 変数展開
  - 展開時点を区別し、必要な範囲だけ遅延展開を有効にする
- セキュリティの一般作法は`implementation-time.md`の「セキュリティ・ロギング・エラー処理」が定める。cmd.exeでは動的な分岐を`if`・`goto`など固定候補への分岐で表す（努力目標）
- 新規スクリプトはPowerShellを優先し、`.cmd`はレガシー互換やPowerShellを使えない環境で選ぶ

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `encoding.md#「書込ツールの改行・BOM保全」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/encoding.md#「書込ツールの改行・BOM保全」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `implementation-time.md#「セキュリティ・ロギング・エラー処理」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/implementation-time.md#「セキュリティ・ロギング・エラー処理」` |
