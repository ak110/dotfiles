# PowerShell記述スタイル

- Claude Codeツールの挙動と注意点
  - EditツールはCRLF改行とUTF-8 BOMを透過的に維持する。既存ファイルの編集はEditを使う
  - Writeツールは常にLF改行・BOMなしで書くため、CRLFとBOMが消失する。PS1ファイルにはWriteを使わない
    - agent-toolkitプラグインがPS1へのLF-only書き込みをブロックするため、Writeは実行自体が失敗する
  - 新規ファイル作成時はBashツールでBOM付きCRLFファイルを書く
    - 例: `printf '\xEF\xBB\xBF' > file.ps1 && cat <<'ENDOFPS1' | sed 's/$/\r/' >> file.ps1`
  - `.gitattributes`の`eol=crlf`は改行のみ管理し、BOMは復元しない。BOM付加は別途必要
- Windows PowerShell 5.1互換性
  - CRLF改行が必須（LFのみだと構文解析に失敗するため）
  - `.gitattributes`で`*.ps1 text eol=crlf`を設定してgit側でも改行を管理する
  - UTF-8エンコーディングを常に明示する
   （Windows PowerShell 5.1のデフォルトエンコーディングはShift-JISであり、日本語が正しく扱えないため）
    - `Get-Content -Encoding UTF8`、`Set-Content -Encoding UTF8`
- 基本スタイル
  - 冒頭に`Set-StrictMode -Version Latest`と`$ErrorActionPreference = 'Stop'`を記述する
  - 命名規則:
    - cmdlet・関数は`Verb-Noun`（PascalCase）
    - 変数は`$camelCase`
    - 承認済み動詞（`Get-Verb`）を使う
- エラーハンドリング
  - `try`／`catch`／`finally`を使う
  - non-terminating errorをキャッチするために`-ErrorAction Stop`を指定する
  - ネイティブexe（`powercfg`・`reg`・`git`・`winget`・`chezmoi`・`uv`等）の呼び出し直後で
    `$LASTEXITCODE`を判定し非ゼロなら`throw`する
    - `$ErrorActionPreference = 'Stop'`はネイティブexeの非ゼロ終了を例外化しないため必要となる
    - 例外: `try/catch`で意図的に失敗を抑止する`best-effort`呼び出しは判定対象外
- パス操作
  - `Join-Path`を使い、文字列結合でパスを組み立てない
- セキュリティ上の危険パターン
  - `Invoke-Expression`はユーザー入力に対して使わない（コマンドインジェクションの危険があるため）
  - 外部入力を含むコマンド文字列を直接実行しない
- COM操作
  - PowerShell 5.1のCOM遅延バインディングでは型変換エラーやDISP_E_TYPEMISMATCH（HRESULT `0x80020005`）が返ることがある
    - エラー例: `型 "int" の "2" 値を型 "Object" に変換できません`
  - `Type.InvokeMember`・`[Type]::Missing`による省略引数補完・C#の`dynamic`は
    同じ遅延バインディング経路を経由するため同様に失敗する
  - Office等のPIA（Primary Interop Assembly）が利用可能な場合は早期バインディングへ切り替える
    - `Add-Type -ReferencedAssemblies @('Microsoft.Office.Interop.PowerPoint', 'Office')`で
      C#ヘルパーを動的コンパイルし、PIA型へキャストして呼び出す
- 他で指定が無い場合のツール推奨:
  - 静的解析: PSScriptAnalyzer
