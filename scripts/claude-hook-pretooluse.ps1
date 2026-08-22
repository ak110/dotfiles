Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

uv run --no-project --script $env:USERPROFILE\dotfiles\scripts\claude_hook.py pretooluse
if ($LASTEXITCODE -eq 2) {
    exit 2
}
exit 0
