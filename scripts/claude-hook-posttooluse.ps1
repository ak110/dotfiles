Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$hook = Get-Command dotfiles-claude-hook -ErrorAction SilentlyContinue
if ($null -eq $hook) {
    exit 0
}

& $hook.Source posttooluse
exit 0
