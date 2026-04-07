Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# install-claude.ps1 - ~/.claude/rules/ に Claude Code 用の共通ルールファイルを配置する。
#
# 会社マシンなど dotfiles 全体を入れられない環境向け。GitHub から最新のルールファイルを
# ダウンロードし、既存ファイルの YAML frontmatter (paths 等のカスタマイズ) は維持する。
#
# cmd からの使い方:
#   powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.ps1 | iex"
#
# テスト時は DOTFILES_RULES_URL 環境変数でベース URL を差し替え可能。

$baseUrl = if ($env:DOTFILES_RULES_URL) { $env:DOTFILES_RULES_URL } else { 'https://raw.githubusercontent.com/ak110/dotfiles/master/.chezmoi-source/dot_claude/rules/agent-basics' }
$targetDir = Join-Path $HOME '.claude/rules/agent-basics'

# 配布対象ファイル一覧 (pytools/claudize.py の _UNCONDITIONAL_RULES / _CONDITIONAL_RULES と一致させること)
$files = @(
    'agent.md',
    'markdown.md',
    'rules.md',
    'skills.md',
    'python.md',
    'python-test.md',
    'typescript.md',
    'typescript-test.md'
)

$script:backupDir = $null
$script:utf8NoBom = New-Object System.Text.UTF8Encoding $false

# pytools/claudize.py:_split_frontmatter と等価。
# 戻り値: @{ HasFm; Frontmatter; Body }
function Split-Frontmatter {
    param([string]$content)
    if (-not $content.StartsWith('---')) {
        return @{ HasFm = $false; Frontmatter = ''; Body = $content }
    }
    $endIdx = $content.IndexOf("`n---", 3)
    if ($endIdx -lt 0) {
        return @{ HasFm = $false; Frontmatter = ''; Body = $content }
    }
    $fmEnd = $endIdx + 4  # "`n---" の長さ
    if ($fmEnd -lt $content.Length -and $content[$fmEnd] -eq "`n") {
        $fmEnd++
    }
    return @{
        HasFm = $true
        Frontmatter = $content.Substring(0, $fmEnd)
        Body = $content.Substring($fmEnd)
    }
}

# URL からダウンロードして UTF-8 文字列として返す。CRLF は LF に正規化。
function Download-String {
    param([string]$url)
    $tmp = [System.IO.Path]::GetTempFileName()
    try {
        $client = New-Object System.Net.WebClient
        try {
            $client.DownloadFile($url, $tmp)
        } finally {
            $client.Dispose()
        }
        $text = [System.IO.File]::ReadAllText($tmp, $script:utf8NoBom)
        return $text -replace "`r`n", "`n"
    } finally {
        Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
    }
}

function Read-TextLF {
    param([string]$path)
    $text = [System.IO.File]::ReadAllText($path, $script:utf8NoBom)
    return $text -replace "`r`n", "`n"
}

function Write-TextLF {
    param([string]$path, [string]$content)
    [System.IO.File]::WriteAllText($path, $content, $script:utf8NoBom)
}

function Invoke-ProcessFile {
    param([string]$name)
    $dst = Join-Path $targetDir $name
    $downloaded = Download-String "$baseUrl/$name"

    $newContent = $downloaded
    $existed = Test-Path -LiteralPath $dst -PathType Leaf
    if ($existed) {
        $existing = Read-TextLF $dst
        $existingFm = Split-Frontmatter $existing
        if ($existingFm.HasFm) {
            $dlFm = Split-Frontmatter $downloaded
            $newContent = $existingFm.Frontmatter + $dlFm.Body
        }
        if ($existing -ceq $newContent) {
            Write-Output "変更なし: $dst"
            return
        }
        if (-not $script:backupDir) {
            # バックアップは ~/.claude/rules/ の外に置く
            # (rules/ 配下は Claude Code が再帰的に読み込むため、退避先も読まれてしまう)
            $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
            $script:backupDir = Join-Path $HOME ".claude/rules-backup/agent-basics-$stamp"
            New-Item -ItemType Directory -Path $script:backupDir -Force | Out-Null
        }
        Copy-Item -LiteralPath $dst -Destination (Join-Path $script:backupDir $name)
        Write-TextLF $dst $newContent
        Write-Output "上書き: $dst"
    } else {
        Write-TextLF $dst $newContent
        Write-Output "追加: $dst"
    }
}

# Main
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
foreach ($file in $files) {
    Invoke-ProcessFile $file
}
if ($script:backupDir) {
    Write-Output "バックアップ先: $script:backupDir"
}
