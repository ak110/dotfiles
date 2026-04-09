#!/bin/bash
# PSScriptAnalyzer 呼び出しラッパー。
#
# 動作:
#   - pwsh 未導入環境: 警告を出力して正常終了 (ローカル dev 環境向け)
#   - pwsh 導入済み:   PSScriptAnalyzer モジュールを必要に応じて導入し Invoke-ScriptAnalyzer を実行
#
# CI (test-linux) では pwsh を事前導入する前提のため、検証漏れは CI で担保する。
set -eu

if ! command -v pwsh >/dev/null 2>&1; then
    echo "pwsh not found, skipping PSScriptAnalyzer" >&2
    exit 0
fi

# pwsh の `-Command` は残りの引数を script 本文に連結するため `-- "$@"` で
# 追加引数を渡すことができない。ファイル一覧は PowerShell 配列リテラルとして
# script に埋め込んでから -Command に渡す。
paths=""
for f in "$@"; do
    # PowerShell single-quoted 文字列のエスケープ (' → '')
    esc=${f//\'/\'\'}
    paths+="'$esc',"
done
paths=${paths%,}

# リポジトリルートに PSScriptAnalyzerSettings.psd1 があれば利用する
# (pre-commit はリポジトリルートから呼び出される前提)。
settings_arg=""
if [ -f "$(pwd)/PSScriptAnalyzerSettings.psd1" ]; then
    settings_esc=${PWD//\'/\'\'}
    settings_arg="-Settings '$settings_esc/PSScriptAnalyzerSettings.psd1'"
fi

# $ErrorActionPreference など PowerShell 側で解釈させたい変数は \$ でエスケープする
exec pwsh -NoProfile -NonInteractive -Command "
    \$ErrorActionPreference = 'Stop'
    if (-not (Get-Module -ListAvailable -Name PSScriptAnalyzer)) {
        Install-Module -Name PSScriptAnalyzer -Scope CurrentUser -Force -SkipPublisherCheck | Out-Null
    }
    Import-Module PSScriptAnalyzer
    # Invoke-ScriptAnalyzer -Path は String 単体しか受けないため、ファイルごとに実行する
    \$results = @($paths) | ForEach-Object { Invoke-ScriptAnalyzer -Path \$_ -Severity @('Error','Warning') $settings_arg }
    if (\$results) {
        \$results | Format-Table -AutoSize
        exit 1
    }
"
