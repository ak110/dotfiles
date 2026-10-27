# Windowsバッチファイル記述スタイル

本書は、cmd.exeで実行されるバッチファイル（`.cmd`および`.bat`）を作成・編集する際の記述スタイル基準をまとめたものである。

- 文字エンコーディング
  - ファイルはCP932（Shift_JIS）で記述する。cmd.exeのコマンドパーサーはシステムのACP（日本語Windows環境ではCP932）に基づいて動作し、スクリプト内で`chcp 65001`を実行してもコンソール入出力のコードページが変わるだけである点に留意する
  - ファイルの読み書き手順は`encoding.md`の「書込ツールの改行・BOM保全」に従う。iconvを使用する場合の文字コード名には`cp932`を指定する（例: `iconv -f cp932 -t utf-8 file.cmd`）。`git show`もCP932のバイト列をそのまま出力するため、文字化けの回避策にはならない
- 改行コード
  - 改行コードはCRLFを必須とする。`.gitattributes`ファイルで`*.cmd text eol=crlf`を指定して改行コードを保持する
- 基本構造
  - `@echo off`などでコマンドのエコー表示を無効化し、`setlocal`を用いて環境変数の影響範囲を限定し、終了ステータス（エラーレベル）を明確に制御する
- 変数展開
  - 即時展開（`%VAR%`）と遅延展開（`!VAR!`）の実行タイミングの違いを意識して使い分け、必要な処理ブロックでのみ遅延環境変数（`setlocal enabledelayedexpansion`）を有効にする
- セキュリティに関する一般的な留意事項は、`implementation-time.md`の「セキュリティ・ロギング・エラー処理」を参照する。cmd.exeでは任意の入力をそのままコマンドとして実行せず、`if`や`goto`を用いてあらかじめ定義した固定の分岐候補へ振り分ける設計とする（努力目標）
- 新しくスクリプトを作成する場合はPowerShellを第一の選択肢とし、`.cmd`は過去のバージョンとの互換性が必要な場合や、PowerShellが利用できない制約がある環境に限定して採用する

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `encoding.md#「書込ツールの改行・BOM保全」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/encoding.md#「書込ツールの改行・BOM保全」` |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `implementation-time.md#「セキュリティ・ロギング・エラー処理」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/implementation-time.md#「セキュリティ・ロギング・エラー処理」` |
