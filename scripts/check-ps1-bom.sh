#!/bin/bash
# .ps1 / .ps1.tmpl ファイルが UTF-8 BOM (EF BB BF) で始まっているかを検証する。
#
# Windows PowerShell 5.1 は BOM なし UTF-8 ファイルを Shift-JIS として誤読し、
# 日本語メッセージが文字化けする。PSScriptAnalyzer の
# PSUseBOMForUnicodeEncodedFile でも検出できるが、pwsh 非導入環境でも
# 検出できるよう独立したチェックとして用意している。
set -eu

fail=0
bom=$'\xef\xbb\xbf'

for file in "$@"; do
    head=$(head -c 3 "$file")
    if [ "$head" != "$bom" ]; then
        echo "FAIL: $file に UTF-8 BOM がありません" >&2
        fail=1
    fi
done

exit $fail
