Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$src = "$env:GITHUB_WORKSPACE\dotfiles"
$dst = "$env:USERPROFILE\dotfiles"
robocopy $src $dst /E /SL /COPY:DAT /NFL /NDL /NJH /NJS
$rc = $LASTEXITCODE
if ($rc -ge 8) {
  Write-Output "::error::robocopyによる配置に失敗: exit $rc、$src -> $dst。コピーのエラーと配置先の権限・空き容量を確認し、修正後にRe-run failed jobsで再実行してください。"
  exit $rc
}
Remove-Item -Recurse -Force $src
"DOTFILES_DIR=$dst" | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8
exit 0
