# PowerShell記述スタイル

本書はPowerShellスクリプトの記述スタイル基準と、Windows PowerShell 5.1互換を保つための注意点を定める。

- Claude Codeツールの挙動と注意点
  - 書込ツールごとの改行・BOMの扱いは`encoding.md`「書込ツールの改行・BOM保全」に従う。PS1ファイルはCRLFとBOMを要するため、既存ファイルはWriteを使わずEditで編集する
- Windows PowerShell 5.1互換性
  - `.gitattributes`で`*.ps1 text eol=crlf`を設定する
  - 非ASCII文字を含むスクリプトはUTF-8 BOM付きで保存する（5.1はBOMの無いファイルをANSIコードページとして読む）
- 基本スタイル
  - StrictMode LatestとErrorActionPreference Stopを基本とし、失敗を検出する
  - cmdlet・関数は承認済み動詞のVerb-Noun（PascalCase）、変数はcamelCaseにそろえる
- エラーハンドリング
  - 例外の原因を保ち、資源を確実に回収する。捕捉が必要なnon-terminating errorはErrorAction Stopで例外化する
  - ネイティブexe（`powercfg`・`reg`・`git`・`winget`・`chezmoi`・`uv`等）の呼び出し直後に`$LASTEXITCODE`を判定し、非ゼロなら`throw`する（`$ErrorActionPreference = 'Stop'`は非ゼロ終了を例外化しない）。意図的に失敗を許す`best-effort`呼び出しは除く
- パス操作
  - パスの構造を保つJoin-Pathを基本とする（努力目標。区切り文字の重複と欠落を避ける）
- COM操作
  - PowerShell 5.1のCOM遅延バインディングは型変換エラーやDISP_E_TYPEMISMATCH（HRESULT `0x80020005`、例: `型 "int" の "2" 値を型 "Object" に変換できません`）を返すことがある。`Type.InvokeMember`・`[Type]::Missing`による省略引数補完・C#の`dynamic`も同じ仕組みを経由して失敗する
  - Office等のPIA（Primary Interop Assembly）が使える場合は早期バインディングへ切り替える。`Add-Type -ReferencedAssemblies @('Microsoft.Office.Interop.PowerPoint', 'Office')`でC#ヘルパーを動的コンパイルし、PIA型へキャストして呼び出す

## 名指し参照の読込方法

本文の各参照の適用条件を保ち、選んだ資料の指定範囲を取得してから、その基準を使う具体的な判断へ進む。起動時の行の資料は起動本文とともに届く。

| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |
| --- | --- | --- | --- |
| 本文がこの資料の基準を使う場面で、資料を選択して取得する時 | `encoding.md#「書込ツールの改行・BOM保全」` | 判断 | `section:agent-basics:*/skills/writing-standards/references/encoding.md#「書込ツールの改行・BOM保全」` |
