# Windowsバッチファイル記述スタイル

cmd.exeで実行するバッチファイル（`.cmd`、`.bat`）を作る時と編集する時の、採用の判断と文字コード・改行の扱いを示す。
原則9（`agent-basics:writing-standards/references/quality-principles-subtraction.md`）の適用として、cmd.exeに固有の決まりだけを置く。

## 基本構造

- 新しいスクリプトはPowerShellで書き、`.cmd`は旧版との互換が要る時とPowerShellを使えない環境に限る。
- 遅延環境変数（`setlocal enabledelayedexpansion`）は`!VAR!`が要るブロックだけで有効にする。
- 任意の入力をそのままコマンドとして実行せず、`if`と`goto`で固定の候補へ振り分ける（努力目標）。

## 文字エンコーディングと改行コード

- ファイルはCP932で書く。
  cmd.exeはシステムのACP（日本語のWindowsではCP932）で解析し、`chcp 65001`はコンソールの入出力のコードページだけを変える。
- 読み書きは`agent-basics:writing-standards/references/encoding.md`「書込ツールの改行・BOM保全」に従い、`iconv`の文字コード名は`cp932`にする。
- `git show`もCP932のバイト列をそのまま出すため、文字化けの回避には使えない。
- 改行はCRLFにし、`.gitattributes`で`*.cmd text eol=crlf`を指定する。
