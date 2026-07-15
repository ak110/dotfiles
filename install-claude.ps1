Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# install-claude.ps1 - ~/.claude/rules/agent-toolkit/ に agent-toolkit ルールファイルを配置する。
#
# 会社マシンなど dotfiles 全体を導入できない環境向け。GitHub から最新のルールファイルを
# 一時ステージングディレクトリへダウンロードし、原子的リネームで配布先を差し替える。
#
# cmd からの使い方 (Claude Code をインストールしたあとで実行する):
#   powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.ps1 | iex"
#
# テスト時は DOTFILES_RULES_URL 環境変数でベース URL を差し替え可能。

$baseUrl = if ($env:DOTFILES_RULES_URL) { $env:DOTFILES_RULES_URL } else { 'https://raw.githubusercontent.com/ak110/dotfiles/master/agent-toolkit/rules' }
$targetDir = Join-Path $HOME '.claude/rules/agent-toolkit'
$legacyDir = Join-Path $HOME '.claude/rules/agent-basics'
# ステージング先は rules/ の外に置く。
# rules/ 配下に配置すると Claude Code が再帰的に読み込むため、差し替え中に二重ロードされる。
$stageRoot = Join-Path $HOME '.claude/rules-stage'

# 配布対象ファイル一覧。
# `install-claude.sh`の`FILES`、および`agent-toolkit/rules/`配下のmdファイル一覧と一致させる。
# 3者の整合性は`agent-toolkit/scripts/install_script_ssot_test.py`で自動検証する。
# 変更時は`uvx pyfltr run-for-agent`を実行してテストgreenを確認する。
$files = @(
    '01-agent.md'
    '02-collaboration.md'
    '03-claude-code.md'
    '04-styles.md'
    '05-terminology.md'
    '06-monitoring.md'
)

function Invoke-Download {
    param([string]$url, [string]$destination)
    $client = New-Object System.Net.WebClient
    try {
        $client.DownloadFile($url, $destination)
    } finally {
        $client.Dispose()
    }
}

# agent-toolkit プラグインを user scope でインストール・更新する。
# 併せて旧 edit-guardrails プラグインを除去する（現在は agent-toolkit に統合されている）。
function Install-AgentToolkitPlugin {
    Write-Output ''
    Write-Output 'agent-toolkit プラグインを user scope にインストール・更新します...'
    try { & claude plugin marketplace add ak110/dotfiles --scope=user 2>&1 | Out-Null } catch { $null = $_ }
    try { & claude plugin marketplace update ak110-dotfiles 2>&1 | Out-Null } catch { $null = $_ }
    try { & claude plugin uninstall 'edit-guardrails@ak110-dotfiles' 2>&1 | Out-Null } catch { $null = $_ }
    try { & claude plugin install 'agent-toolkit@ak110-dotfiles' --scope=user 2>&1 | Out-Null } catch { $null = $_ }
    try { & claude plugin update 'agent-toolkit@ak110-dotfiles' --scope=user 2>&1 | Out-Null } catch { $null = $_ }
    Write-Output 'agent-toolkit プラグインの導入・更新を試行しました (旧 edit-guardrails は削除を試行しました)。'
}

# ~/.local/bin/atk.cmd へラッパーを配置する。
# インストール済み agent-toolkit プラグインの最新バージョンを動的解決するため、
# 参照先パス（cache/<marketplace>/agent-toolkit/<version>/bin/atk.cmd）を実行時に決定する形とする。
# 直接コピーするとバージョン更新のたびに追随が必要となるため、実行時解決のラッパーを採用する。
function Install-AtkWrapper {
    $binDir = Join-Path $HOME '.local/bin'
    $wrapper = Join-Path $binDir 'atk.cmd'
    New-Item -ItemType Directory -Path $binDir -Force | Out-Null
    $body = @'
@echo off
setlocal enabledelayedexpansion
set "PLUGIN_ROOT=%USERPROFILE%\.claude\plugins\cache"
set "LATEST="
for /f "delims=" %%A in ('dir /b /ad /o-n "%PLUGIN_ROOT%\*\agent-toolkit" 2^>nul') do (
    for /f "delims=" %%B in ('dir /b /ad /o-n "%PLUGIN_ROOT%\%%A\*" 2^>nul') do (
        if not defined LATEST set "LATEST=%PLUGIN_ROOT%\%%A\%%B\bin\atk.cmd"
    )
)
if not defined LATEST (
    echo atk: agent-toolkit プラグインが見つかりません。install-claude.ps1 を再実行してください。 1>&2
    exit /b 1
)
call "%LATEST%" %*
'@
    Set-Content -LiteralPath $wrapper -Value $body -Encoding ASCII
    Write-Output "配置: $wrapper"
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not ($userPath -split ';' | Where-Object { $_ -ieq $binDir })) {
        Write-Warning "'$binDir' がユーザー PATH に含まれていません。setx PATH ""%PATH%;$binDir"" 等で追加してください。"
    }
}

function Main {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
        Write-Error 'Claude Code (claude CLI) が見つかりません。Claude Code を先にインストールしてから本スクリプトを再実行してください。'
        exit 1
    }

    New-Item -ItemType Directory -Path (Split-Path $targetDir -Parent) -Force | Out-Null
    New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null

    $stageDir = Join-Path $stageRoot ("agent-toolkit.stage." + [System.IO.Path]::GetRandomFileName())
    New-Item -ItemType Directory -Path $stageDir -Force | Out-Null

    $oldDir = $null
    $replaced = $false
    try {
        foreach ($name in $files) {
            Invoke-Download "$baseUrl/$name" (Join-Path $stageDir $name)
        }

        # リネーム前の旧ファイル名を配布先から削除する（ダウンロード対象一覧との差分残置防止）。
        foreach ($legacy in @('02-claude-code.md', '03-styles.md', '04-terminology.md')) {
            Remove-Item -Path (Join-Path $targetDir $legacy) -Force -ErrorAction SilentlyContinue
        }

        if (Test-Path -LiteralPath $targetDir) {
            $oldDir = Join-Path $stageRoot ("agent-toolkit.old." + [System.IO.Path]::GetRandomFileName())
            Move-Item -LiteralPath $targetDir -Destination $oldDir
        }
        Move-Item -LiteralPath $stageDir -Destination $targetDir
        $stageDir = $null
        $replaced = $true
        Write-Output "配置: $targetDir"

        if ($oldDir -and (Test-Path -LiteralPath $oldDir)) {
            Remove-Item -LiteralPath $oldDir -Recurse -Force
            $oldDir = $null
        }

        if (Test-Path -LiteralPath $legacyDir) {
            Remove-Item -LiteralPath $legacyDir -Recurse -Force
            Write-Output "削除（旧ディレクトリ）: $legacyDir"
        }

        Install-AgentToolkitPlugin
        Install-AtkWrapper
    } finally {
        # 差し替え前にエラー終了した場合、既存環境を復元する。
        if (-not $replaced -and $oldDir -and (Test-Path -LiteralPath $oldDir) -and -not (Test-Path -LiteralPath $targetDir)) {
            try { Move-Item -LiteralPath $oldDir -Destination $targetDir -ErrorAction Stop; $oldDir = $null } catch { $null = $_ }
        }
        if ($stageDir -and (Test-Path -LiteralPath $stageDir)) {
            Remove-Item -LiteralPath $stageDir -Recurse -Force -ErrorAction SilentlyContinue
        }
        if ($oldDir -and (Test-Path -LiteralPath $oldDir)) {
            Remove-Item -LiteralPath $oldDir -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

Main
