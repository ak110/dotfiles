# PowerShell記述スタイル

PowerShellのスクリプトを書く時とレビューする時の、Windows PowerShell 5.1との互換、外部コマンドの失敗の検知、COMの呼び出し方を示す。
原則9（`agent-basics:writing-standards/references/quality-principles-subtraction.md`）の適用として、PowerShellに固有の決まりだけを置く。
PSScriptAnalyzerが導入されている環境では、その診断に従う。

## 改行と文字コード

- `.ps1`はCRLFとBOMを要するため、既存のファイルは`Edit`で編集する（`agent-basics:writing-standards/references/encoding.md`「書込ツールの改行・BOM保全」）。
- `.gitattributes`で`*.ps1 text eol=crlf`を指定する。
- 非ASCII文字を含むスクリプトはBOM付きのUTF-8で保存する（`PSUseBOMForUnicodeEncodedFile`）。
  Windows PowerShell 5.1は、BOMの無いファイルをANSIコードページで読む。

## 基本スタイル

- 先頭で`Set-StrictMode -Version Latest`と`$ErrorActionPreference = 'Stop'`を設定する。
- 関数名は承認済みの動詞のVerb-Noun形式（PascalCase。`PSUseApprovedVerbs`）、変数名はcamelCaseにする。

## エラーハンドリング

- 捕捉したい非終了エラーは`-ErrorAction Stop`で例外にする。
- 外部の実行ファイルを実行した直後は`$LASTEXITCODE`を確かめ、0以外なら`throw`する（失敗を許す呼び出しは省いてよい）。
  外部コマンドの0以外の終了は`$ErrorActionPreference = 'Stop'`でも例外にならず、`$LASTEXITCODE`だけに残る。

## COM操作

- 共有Office文書では、最初の変更前に対象のアプリと保存先で自動保存・同期の状態を確かめる。
  例えばExcelでは対象ブックの`AutoSaveOn`を行の追加より先に確認する。
  後続のCOM呼び出しが失敗しても、明示的な保存を待たずに空行が同期されうる。
  編集先の選択と設定の復元は`agent-basics:writing-standards/references/quality-principles-constraints.md`「共有Office文書の編集」に従う。
- PowerShell 5.1のCOMの遅延バインディングでは、型変換のエラーや`DISP_E_TYPEMISMATCH`（HRESULT `0x80020005`。例: `型 "int" の "2" 値を型 "Object" に変換できません`）が起きることがある。
- `Type.InvokeMember`、`[Type]::Missing`による省略時の引数の補完、C#の`dynamic`も同じ内部の仕組みを経由し、同じく失敗する。
- OfficeなどのPIA（Primary Interop Assembly）を使える環境では、早期バインディングで呼ぶ。
  `Add-Type -ReferencedAssemblies @('Microsoft.Office.Interop.PowerPoint', 'Office')`でC#の補助コードをコンパイルし、PIAの型へキャストする。
