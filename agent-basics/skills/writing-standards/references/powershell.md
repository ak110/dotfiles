# PowerShell記述スタイル

本書は、PowerShellスクリプトの記述スタイル基準と、Windows PowerShell 5.1との互換性を保つための注意点をまとめたものである。

- Claude Codeツールの挙動と注意点
  - ツールごとの改行コードおよびBOMの扱いは、`encoding.md`「書込ツールの改行・BOM保全」に従う。PS1ファイルにはCRLF改行とBOMが必要なため、既存ファイルを編集する際はEditを使用する。
- Windows PowerShell 5.1との互換性
  - `.gitattributes`で`*.ps1 text eol=crlf`を指定する。
  - 非ASCII文字を含むスクリプトは、UTF-8（BOM付き）で保存する（Windows PowerShell 5.1はBOMのないファイルをANSIコードページとして読み込むため）。
- 基本スタイル
  - エラーを確実に検知するため、`Set-StrictMode -Version Latest`と`$ErrorActionPreference = 'Stop'`を基本とする。
  - コマンドレットや関数名は承認済み動詞を用いたVerb-Noun形式（PascalCase）とし、変数名はcamelCaseで統一する。
- エラーハンドリング
  - 例外発生時は原因情報を保持し、確保したリソースを確実に解放する。捕捉が必要な非終了エラー（non-terminating error）は、`-ErrorAction Stop`を指定して例外として扱う。
  - 外部の実行可能ファイル（`powercfg`、`reg`、`git`、`winget`、`chezmoi`、`uv`など）を実行した直後は`$LASTEXITCODE`を確認し、0以外であれば`throw`で例外を発生させる。これは`$ErrorActionPreference = 'Stop'`では外部コマンドの非ゼロ終了を例外化できないためである。失敗を許容する設計の呼び出し（best-effort）はこの確認を省略してよい。
- パス操作
  - 区切り文字の重複や欠落を防ぐため、パスの結合には`Join-Path`を使用する（努力目標。パス文字列の整合性を保つため）。
- COM操作
  - PowerShell 5.1のCOM遅延バインディングでは、型変換エラーや`DISP_E_TYPEMISMATCH`（HRESULT `0x80020005`、例: `型 "int" の "2" 値を型 "Object" に変換できません`）が発生する場合がある。`Type.InvokeMember`や`[Type]::Missing`による省略時引数の補完、C#の`dynamic`も同じ内部機構を経由するため同様に失敗する。
  - OfficeなどのPIA（Primary Interop Assembly）を利用できる環境では、早期バインディングを利用する。`Add-Type -ReferencedAssemblies @('Microsoft.Office.Interop.PowerPoint', 'Office')`でC#ヘルパーを動的にコンパイルし、PIAの型へキャストして呼び出す。

## 名指し参照の読込方法

名指し参照は`../SKILL.md`「名指し参照の読込方法」に従う。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `encoding.md#「書込ツールの改行・BOM保全」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/encoding.md#「書込ツールの改行・BOM保全」` |
