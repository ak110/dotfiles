Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# フック本体を解決できない場合は判定せず通過させる。
# 解決失敗をuvの終了コード2として受け取ると、判定による遮断と区別できずツール呼び出しが止まる。
$hookScript = Join-Path $env:USERPROFILE 'dotfiles\scripts\claude_hook.py'
if (-not (Test-Path -LiteralPath $hookScript -PathType Leaf)) {
    exit 0
}

uv run --no-project --script $hookScript pretooluse
if ($LASTEXITCODE -eq 2) {
    exit 2
}
exit 0
