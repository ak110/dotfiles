# install.ps1 - dotfiles を初回インストールする (Windows PowerShell 向け)。
#
# install.sh と同等の処理を行う:
#   1. 前提条件 (git, uv) のチェック
#   2. chezmoi が未インストールなら自動取得
#   3. ~/dotfiles を clone (既に存在すればスキップ)
#   4. 既存 dotfiles をバックアップ
#   5. chezmoi init --apply で適用
#
# 使い方:
#   powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/ak110/dotfiles/master/install.ps1 | iex"
#
# README の「Windows」セクションには winget を使う簡易版も残してある。
# winget を避けたい/採用できない場合はこのスクリプトを利用する。
#
# NOTE: 対応するLinux版 → install.sh

param([ValidateSet('full', 'minimal')][string]$Mode)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# 前提条件チェック。
$missing = @()
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { $missing += 'git' }
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { $missing += 'uv' }
if ($missing.Count -gt 0) {
    Write-Error ("前提条件が未インストールです: {0}`n" -f ($missing -join ', ') +
        "README の「前提条件(要インストール)」セクションを参照してインストールしてください:`n" +
        "  https://github.com/ak110/dotfiles#前提条件要インストール")
}

# chezmoi が無ければ winget で導入する（winget 自体は Windows 10/11 標準搭載）
if (-not (Get-Command chezmoi -ErrorAction SilentlyContinue)) {
    Write-Host 'chezmoi をインストールします...'
    winget install --id=twpayne.chezmoi -e --source=winget
    if ($LASTEXITCODE -ne 0) { throw "winget install が失敗しました (exit=$LASTEXITCODE)" }
}

$dotfilesDir = Join-Path $env:USERPROFILE 'dotfiles'
if (-not (Test-Path $dotfilesDir)) {
    Write-Host "$dotfilesDir にリポジトリを clone します..."
    git clone https://github.com/ak110/dotfiles.git $dotfilesDir
    if ($LASTEXITCODE -ne 0) { throw "git clone が失敗しました (exit=$LASTEXITCODE)" }
}

# 初回のテンプレート評価より前にモードを保存する。
$modeArguments = @()
if ($Mode) { $modeArguments = @('--mode', $Mode) }
$modeScript = Join-Path $dotfilesDir 'agent-toolkit/agent_toolkit/_common/dotfiles_mode.py'
$selectedMode = & uv run --no-project --no-config --python 3.15 python $modeScript @modeArguments
if ($LASTEXITCODE -ne 0) { throw "モード設定に失敗しました (exit=$LASTEXITCODE)" }
$env:AGENT_TOOLKIT_CONFIG_DOTFILES_MODE = $selectedMode

# chezmoi 管理対象の既存ファイルをバックアップする。
$backupDir = Join-Path $env:USERPROFILE (".dotfiles-backup\{0}" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
$count = 0
$managed = & chezmoi managed --source=$dotfilesDir
if ($LASTEXITCODE -ne 0) { throw "chezmoi managed が失敗しました (exit=$LASTEXITCODE)" }
foreach ($target in $managed) {
    if (-not $target) { continue }
    $src = Join-Path $env:USERPROFILE $target
    if (-not (Test-Path $src -PathType Leaf)) { continue }
    $destDir = Join-Path $backupDir (Split-Path -Parent $target)
    if (-not (Test-Path $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }
    Copy-Item -Path $src -Destination $destDir -Force
    $count++
}
if ($count -gt 0) {
    Write-Host "既存ファイル $count 件を $backupDir にバックアップしました"
}

chezmoi init --verbose --source=$dotfilesDir --apply
if ($LASTEXITCODE -ne 0) { throw "chezmoi init が失敗しました (exit=$LASTEXITCODE)" }
