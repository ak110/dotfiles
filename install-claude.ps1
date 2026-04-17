Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# install-claude.ps1 - ~/.claude/rules/agent-toolkit/ に agent-toolkit ルールファイルを配置する。
#
# 会社マシンなど dotfiles 全体を入れられない環境向け。GitHub から最新のルールファイルを
# 一時ステージングディレクトリへダウンロードし、原子的リネームで配布先を差し替える。
#
# cmd からの使い方 (Claude Code をインストールしたあとで実行する):
#   powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.ps1 | iex"
#
# テスト時は DOTFILES_RULES_URL 環境変数でベース URL を差し替え可能。

$baseUrl = if ($env:DOTFILES_RULES_URL) { $env:DOTFILES_RULES_URL } else { 'https://raw.githubusercontent.com/ak110/dotfiles/master/.chezmoi-source/dot_claude/rules/agent-toolkit' }
$targetDir = Join-Path $HOME '.claude/rules/agent-toolkit'
$legacyDir = Join-Path $HOME '.claude/rules/agent-basics'
# ステージング先は rules/ の外に置く。
# rules/ 配下に作ると Claude Code が再帰的に読み込んでしまい、差し替え中に二重ロードされる危険がある。
$stageRoot = Join-Path $HOME '.claude/rules-stage'

# 配布対象ファイル一覧 (install-claude.sh の FILES と一致させること)
$files = @(
    'agent.md',
    'styles.md'
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
# 併せて旧 edit-guardrails プラグインを除去する (agent-toolkit へ改名・統合されたため)。
function Install-AgentToolkitPlugin {
    Write-Output ''
    Write-Output 'agent-toolkit プラグインを user scope にインストール・更新します...'
    try { & claude plugin marketplace add ak110/dotfiles --scope user 2>&1 | Out-Null } catch { $null = $_ }
    try { & claude plugin marketplace update ak110-dotfiles 2>&1 | Out-Null } catch { $null = $_ }
    try { & claude plugin uninstall 'edit-guardrails@ak110-dotfiles' 2>&1 | Out-Null } catch { $null = $_ }
    try { & claude plugin install 'agent-toolkit@ak110-dotfiles' --scope user 2>&1 | Out-Null } catch { $null = $_ }
    try { & claude plugin update 'agent-toolkit@ak110-dotfiles' --scope user 2>&1 | Out-Null } catch { $null = $_ }
    Write-Output 'agent-toolkit プラグインの導入・更新を試行しました (旧 edit-guardrails は削除を試行しました)。'
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
    } finally {
        # 差し替え前にエラー終了した場合は既存環境を復元する。
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
