param([switch]$BasicsOnly)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# install-claude.ps1 - dotfiles 全体を導入できない環境へ agent-toolkit を導入・更新する。
#
# 会社マシンなど dotfiles 全体を導入できない環境向け。次を順に行う。
# - 旧版が配置した常時規範（~/.claude/rules/agent-toolkit/ など）を削除する
# - Claude Code と Codex へ agent-toolkit プラグインを導入・更新する
# - agents_server の依存をウォームアップする
# - 旧 Codex MCP 定義（User scope）を移行する
# - ~/.local/bin/atk.cmd へラッパーを配置する
#
# cmd からの使い方 (Claude Code、Codex、uv をインストールしたあとで実行する):
#   powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/ak110/dotfiles/master/install-claude.ps1 | iex"
#
# テスト時は DOTFILES_RAW_BASE_URL 環境変数でリポジトリのファイルの取得元を差し替え可能。

$rawBaseUrl = if ($env:DOTFILES_RAW_BASE_URL) { $env:DOTFILES_RAW_BASE_URL } else { 'https://raw.githubusercontent.com/ak110/dotfiles/master' }
# 旧版が配置した常時規範の配置先。規範は入口のスキル`atk-mode`と手順書へ移したため、残すと廃止した
# スキルの起動を求める旧い規範が読まれ続ける。いずれもインストーラーだけが書き込んでいた。
$retiredPaths = @(
    (Join-Path $HOME '.claude/rules/agent-toolkit')
    (Join-Path $HOME '.claude/rules/agent-basics')
    (Join-Path $HOME '.claude/rules-stage')
)
$codexPluginId = 'agent-toolkit@ak110-dotfiles'
$basicsPluginId = 'agent-basics@ak110-dotfiles'
$claudeInstalledPluginsPath = Join-Path $HOME '.claude/plugins/installed_plugins.json'
$script:codexPluginUpdated = $false
$script:utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$codexHomeDir = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME '.codex' }
$codexPluginCacheRoot = Join-Path $codexHomeDir 'plugins/cache/ak110-dotfiles/agent-toolkit'
$savedCacheRetry = '次回の実行が退避先から戻します。解消しない場合は退避先を変更せず、表示された内容とパスをdotfilesの作者へ報告してください。'

function Invoke-Download {
    param([string]$url, [string]$destination)
    $client = New-Object System.Net.WebClient
    try {
        $client.DownloadFile($url, $destination)
    } finally {
        $client.Dispose()
    }
}

function Assert-Command {
    param([string]$name, [string]$productName, [string]$installUrl)
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "$productName ($name CLI) が見つかりません。$installUrl を参照して先にインストールしてください。"
    }
}

function Invoke-RequiredNativeCommand {
    param([string]$command, [string[]]$arguments)
    & $command @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$command の実行に失敗しました（終了コード: $LASTEXITCODE）。"
    }
}

# 失敗すると導入を続けられない Claude CLI の更新を実行する。
# 成功時は出力を抑え、失敗時は捕捉した標準出力・標準エラーの元診断、失敗したコマンド、終了コード、次の操作を例外へ含める。
function Invoke-RequiredClaudeCommand {
    param([string]$failureMessage, [string[]]$arguments)
    # Windows PowerShell 5.1では、Stopのまま標準エラーを`2>&1`で受けると最初の1行が終了エラーになり、
    # 残りの診断と終了コードを取得できない。この関数の中だけContinueへ下げて全出力を受け取る。
    $ErrorActionPreference = 'Continue'
    $output = & claude @arguments 2>&1
    $nativeExitCode = $LASTEXITCODE
    if ($nativeExitCode -eq 0) { return }
    $lines = @($output | ForEach-Object { "$_" })
    $lines += $failureMessage
    $lines += "失敗したコマンド: claude $($arguments -join ' ')（終了コード: $nativeExitCode）"
    $lines += '次の操作: 上の診断から原因を解消し、失敗したコマンドを再実行して成功を確認してから、install-claude.ps1を再実行してください。解消できない場合は、表示された診断と失敗したコマンドをdotfilesの作者へ報告してください。'
    throw ($lines -join [Environment]::NewLine)
}

# 併せて旧 edit-guardrails プラグインを除去する（現在は agent-toolkit に統合されている）。
function Install-AgentToolkitPlugin {
    Write-Output ''
    Write-Output 'agent-toolkit プラグインを user scope にインストール・更新します...'
    & claude plugin marketplace add ak110/dotfiles --scope=user 2>&1 | Out-Null
    Invoke-RequiredClaudeCommand 'Claude Codeマーケットプレイスの更新に失敗しました。' @('plugin', 'marketplace', 'update', 'ak110-dotfiles')
    & claude plugin uninstall 'edit-guardrails@ak110-dotfiles' 2>&1 | Out-Null
    & claude plugin install 'agent-toolkit@ak110-dotfiles' --scope=user 2>&1 | Out-Null
    Invoke-RequiredClaudeCommand 'Claude Codeプラグインの更新に失敗しました。' @('plugin', 'update', 'agent-toolkit@ak110-dotfiles', '--scope=user')
    Write-Output 'Claude Code側のagent-toolkitプラグインを設定しました。'
}

function Install-AgentBasicsPlugin {
    Write-Output 'agent-basics プラグインを user scope にインストール・更新します...'
    & claude plugin marketplace add ak110/dotfiles --scope=user 2>&1 | Out-Null
    Invoke-RequiredClaudeCommand 'Claude Codeマーケットプレイスの更新に失敗しました。' @('plugin', 'marketplace', 'update', 'ak110-dotfiles')
    & claude plugin install $basicsPluginId --scope=user 2>&1 | Out-Null
    Invoke-RequiredClaudeCommand 'Claude Codeのagent-basicsプラグイン更新に失敗しました。' @('plugin', 'update', $basicsPluginId, '--scope=user')
}

function Get-CodexPluginState {
    param([string]$pluginId = $codexPluginId)
    $jsonLines = & codex plugin list --json --marketplace ak110-dotfiles 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    try {
        $data = ($jsonLines -join "`n") | ConvertFrom-Json -ErrorAction Stop
    } catch {
        return $null
    }
    if ($null -eq $data -or $data.GetType() -ne [System.Management.Automation.PSCustomObject]) { return $null }
    $installedProperty = $data.PSObject.Properties['installed']
    if ($null -eq $installedProperty -or $installedProperty.Value -isnot [System.Array]) { return $null }
    $plugin = $null
    foreach ($item in @($installedProperty.Value)) {
        if ($null -eq $item -or $item.GetType() -ne [System.Management.Automation.PSCustomObject]) { return $null }
        $pluginIdProperty = $item.PSObject.Properties['pluginId']
        if ($null -eq $pluginIdProperty -or $pluginIdProperty.Value -isnot [string]) { return $null }
        if ($pluginIdProperty.Value -ne $pluginId) { continue }
        $versionProperty = $item.PSObject.Properties['version']
        $enabledProperty = $item.PSObject.Properties['enabled']
        if ($null -eq $versionProperty -or $versionProperty.Value -isnot [string] -or
            $null -eq $enabledProperty -or $enabledProperty.Value -isnot [bool]) {
            return $null
        }
        if ($null -eq $plugin) { $plugin = $item }
    }
    if ($null -eq $plugin) {
        return [PSCustomObject]@{ Present = $false; Version = $null; Enabled = $null }
    }
    return [PSCustomObject]@{
        Present = $true
        Version = $plugin.PSObject.Properties['version'].Value
        Enabled = $plugin.PSObject.Properties['enabled'].Value
    }
}

# 期待する版はGitHubのmarketplace（ak110/dotfiles）を登録したCodexが取得したmanifestから読む。
# dotfilesのpost-applyはローカルの作業ツリーをmarketplaceとして登録し、その版を期待値にする。
# 単体インストーラーは作業ツリーを前提にできないため、配布元の違いをそのまま残す。
function Get-CodexExpectedPluginVersion {
    param([string]$pluginName = 'agent-toolkit')
    $jsonLines = & codex plugin marketplace list --json 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Codexマーケットプレイス一覧を取得できません。' }
    try {
        $data = ($jsonLines -join "`n") | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw 'Codexマーケットプレイス一覧をJSONとして読み取れません。'
    }
    foreach ($marketplace in @($data.marketplaces)) {
        if ($marketplace.name -ne 'ak110-dotfiles' -or $marketplace.root -isnot [string]) { continue }
        $manifestPath = '<未解決>'
        try {
            $rootPath = (Resolve-Path -LiteralPath $marketplace.root -ErrorAction Stop).Path
            $marketplacePath = Join-Path $rootPath '.agents/plugins/marketplace.json'
            $localMarketplace = Get-Content -LiteralPath $marketplacePath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
            $entry = @($localMarketplace.plugins) | Where-Object { $_.name -eq $pluginName } | Select-Object -First 1
            if ($null -eq $entry -or $entry.source.source -ne 'local' -or $entry.source.path -isnot [string]) { throw 'invalid source' }
            $pluginRoot = (Resolve-Path -LiteralPath (Join-Path $rootPath $entry.source.path) -ErrorAction Stop).Path
            $relativePluginRoot = [IO.Path]::GetRelativePath($rootPath, $pluginRoot)
            if ($relativePluginRoot -eq '..' -or $relativePluginRoot.StartsWith("..$([IO.Path]::DirectorySeparatorChar)")) {
                throw 'source outside marketplace root'
            }
            $manifestPath = Join-Path $pluginRoot '.codex-plugin/plugin.json'
            $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        } catch {
            throw "$manifestPath からCodex pluginのversionを取得できません。"
        }
        if ($manifest.name -ne $pluginName) { throw "$manifestPath のnameが期待値と一致しません。" }
        if ($manifest.version -isnot [string]) { throw "$manifestPath のversionが文字列ではありません。" }
        return $manifest.version
    }
    throw 'ak110-dotfilesマーケットプレイスの登録先を取得できません。'
}

function Test-CodexPluginStateChanged {
    param([object]$beforeState, [object]$afterState)
    if ($null -eq $beforeState -or $null -eq $afterState) { return $false }
    return $beforeState.Present -ne $afterState.Present -or
        $beforeState.Version -ne $afterState.Version -or
        $beforeState.Enabled -ne $afterState.Enabled
}

# 旧versionはCODEX_HOME直下の固定の退避先へ移動し、使用中や別ボリュームで移動できない版だけ複製する。
# 中断した実行が残した退避は、次回の実行がRestore-CodexPluginCacheSnapshotで戻す。
# 目標versionはCLIが置き換えるため元の場所に残して複製し、plugin addの失敗時に戻せるようにする。
# 移動できない旧versionも元の場所に残して複製し、CLIが途中まで消した場合に復元で補えるよう~copiedの印を付ける。
function Move-CodexPluginCacheSnapshot {
    param([string]$destination, [string]$targetVersion)
    if (-not (Test-Path -LiteralPath $codexPluginCacheRoot -PathType Container)) { return }
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    foreach ($cache in @(Get-ChildItem -LiteralPath $codexPluginCacheRoot -Directory)) {
        if ($cache.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
        # 復元途中の複製（~restoring）など、版でない名前は退避しない。
        if ($cache.Name.Contains('~')) { continue }
        $saved = Join-Path $destination $cache.Name
        if ($cache.Name -ne $targetVersion) {
            try { [IO.Directory]::Move($cache.FullName, $saved) } catch { Write-Verbose "移動できないため複製する: $($cache.FullName)" }
            if (Test-Path -LiteralPath $cache.FullName) {
                New-Item -ItemType File -Path "$saved~copied" -Force | Out-Null
            }
        }
        if (Test-Path -LiteralPath $cache.FullName) {
            Copy-Item -LiteralPath $cache.FullName -Destination $saved -Recurse
        }
    }
}

# 退避した版にあって元のパスに無いファイルとディレクトリを補う。
# 元のパスに残るファイルはCLIが消せなかった使用中のファイルであり、置き換えずに残す。
# 各ファイルは隣で複製を仕上げてから名前の変更で置き、途中で止まっても欠けたファイルを元の名前で残さない。
function Complete-CodexPluginCacheFromSnapshot {
    param([string]$saved, [string]$destination)
    $savedFull = (Get-Item -LiteralPath $saved -Force).FullName
    foreach ($item in @(Get-ChildItem -LiteralPath $savedFull -Recurse -Force)) {
        $target = Join-Path $destination $item.FullName.Substring($savedFull.Length).TrimStart('\', '/')
        if ($item.PSIsContainer) {
            New-Item -ItemType Directory -Path $target -Force | Out-Null
            continue
        }
        if (Test-Path -LiteralPath $target) { continue }
        $staging = "$target~restoring"
        if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Force }
        Copy-Item -LiteralPath $item.FullName -Destination $staging
        Move-Item -LiteralPath $staging -Destination $target
    }
}

function Restore-CodexPluginCacheSnapshot {
    param([string]$savedRoot, [string]$installedVersion, [bool]$replaceExisting)
    if (-not (Test-Path -LiteralPath $savedRoot -PathType Container)) { return }
    foreach ($saved in @(Get-ChildItem -LiteralPath $savedRoot -Directory)) {
        $destination = Join-Path $codexPluginCacheRoot $saved.Name
        # 置き換えの途中で失敗した版は印を残し、次回の実行も途中まで消えた元のパスより退避した内容を優先する。
        $replaceMarker = Join-Path $savedRoot "$($saved.Name)~replace"
        # 複製で退避した使用中の版は使用中のファイルが残って置き換えられないため、元のパスに無いファイルだけを補う。
        $copiedMarker = Join-Path $savedRoot "$($saved.Name)~copied"
        $copied = Test-Path -LiteralPath $copiedMarker
        $staging = "$destination~restoring"
        if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
        if (($replaceExisting -or (Test-Path -LiteralPath $replaceMarker)) -and -not $copied -and (Test-Path -LiteralPath $destination)) {
            New-Item -ItemType File -Path $replaceMarker -Force | Out-Null
            Remove-Item -LiteralPath $destination -Recurse -Force
        }
        if ($saved.Name -eq $installedVersion -or (Test-Path -LiteralPath $destination)) {
            if ($copied -and $saved.Name -ne $installedVersion) {
                Complete-CodexPluginCacheFromSnapshot $saved.FullName $destination
            }
            Remove-Item -LiteralPath $replaceMarker, $copiedMarker -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $saved.FullName -Recurse -Force
            continue
        }
        New-Item -ItemType Directory -Path $codexPluginCacheRoot -Force | Out-Null
        try {
            [IO.Directory]::Move($saved.FullName, $destination)
            Remove-Item -LiteralPath $replaceMarker, $copiedMarker -Force -ErrorAction SilentlyContinue
        } catch {
            # cacheが別ボリュームへのリンクやjunctionの配下にあると移動できないため、
            # 複製を仕上げてから元のパスへ置き、その後に退避を削除する。
            Copy-Item -LiteralPath $saved.FullName -Destination $staging -Recurse
            [IO.Directory]::Move($staging, $destination)
            Remove-Item -LiteralPath $replaceMarker, $copiedMarker -Force -ErrorAction SilentlyContinue
            Remove-Item -LiteralPath $saved.FullName -Recurse -Force
        }
    }
    # 版を元のパスへ戻した直後に中断すると、対応する退避の無い印だけが残る。
    Get-ChildItem -LiteralPath $savedRoot -File -Force | Where-Object { $_.Name -like '*~replace' -or $_.Name -like '*~copied' } | Remove-Item -Force
    # 退避先は名前が.で始まり、Linux・macOSのPowerShellでは隠し項目になるため-Forceで削除する。
    Remove-Item -LiteralPath $savedRoot -Force
    foreach ($parent in @((Split-Path -Parent $savedRoot), (Split-Path -Parent (Split-Path -Parent $savedRoot)))) {
        if (-not (Get-ChildItem -LiteralPath $parent -Force)) { Remove-Item -LiteralPath $parent -Force }
    }
}

function Install-CodexPlugin {
    param([string]$pluginName = 'agent-toolkit')
    $codexPluginId = "$pluginName@ak110-dotfiles"
    $codexPluginCacheRoot = Join-Path $codexHomeDir "plugins/cache/ak110-dotfiles/$pluginName"
    Write-Output "Codex側の$pluginName プラグインを設定します..."
    $savedCacheRoot = Join-Path $codexHomeDir ".dotfiles-plugin-cache-saved/ak110-dotfiles/$pluginName"
    try {
        Restore-CodexPluginCacheSnapshot $savedCacheRoot '' $false
    } catch {
        throw "前回の実行が退避した旧cacheの復旧に失敗しました。退避先を保持します: $savedCacheRoot。$($_.Exception.Message) $savedCacheRetry"
    }
    & codex plugin marketplace add ak110/dotfiles --json 2>&1 | Out-Null
    Invoke-RequiredNativeCommand codex @('plugin', 'marketplace', 'upgrade', 'ak110-dotfiles', '--json')
    $expectedVersion = Get-CodexExpectedPluginVersion $pluginName
    $beforeState = Get-CodexPluginState $codexPluginId
    if ($null -eq $beforeState) { throw 'Codex plugin更新前の状態を確認できません。' }
    if ($pluginName -eq 'agent-toolkit') {
    $binDir = Join-Path $HOME '.local/bin'
    New-Item -ItemType Directory -Path $binDir -Force | Out-Null
    $hookWrapper = Join-Path $binDir 'atk-hook.cmd'
    Invoke-Download "$rawBaseUrl/agent-toolkit/scripts/atk-hook" (Join-Path $binDir 'atk-hook')
    Invoke-Download "$rawBaseUrl/agent-toolkit/scripts/atk-hook.cmd" $hookWrapper
    }
    try {
        Move-CodexPluginCacheSnapshot $savedCacheRoot $expectedVersion
    } catch {
        $snapshotError = $_
        try {
            Restore-CodexPluginCacheSnapshot $savedCacheRoot '' $false
        } catch {
            throw "Codex plugin更新前の旧cacheの退避と復旧に失敗しました。退避先を保持します: $savedCacheRoot。$($_.Exception.Message) $savedCacheRetry"
        }
        throw $snapshotError
    }
    try {
        Invoke-RequiredNativeCommand codex @('plugin', 'add', $codexPluginId, '--json')
    } catch {
        $addError = $_
        try {
            Restore-CodexPluginCacheSnapshot $savedCacheRoot '' $true
        } catch {
            throw "Codex plugin addと旧cacheの復旧に失敗しました。退避先を保持します: $savedCacheRoot。$($_.Exception.Message) $savedCacheRetry"
        }
        throw $addError
    }
    try {
        $afterState = Get-CodexPluginState $codexPluginId
        if ($pluginName -eq 'agent-toolkit') {
            $script:codexPluginUpdated = Test-CodexPluginStateChanged $beforeState $afterState
        }
        if ($null -eq $afterState -or -not $afterState.Present -or -not $afterState.Enabled -or $afterState.Version -ne $expectedVersion) {
            throw 'Codex plugin更新後の状態が期待値と一致しません。'
        }
        if ($pluginName -eq 'agent-toolkit' -and -not (Test-Path -LiteralPath (Join-Path $codexPluginCacheRoot "$expectedVersion/agent_toolkit/hook.py") -PathType Leaf)) {
            throw 'Codex plugin更新後のhook実体を確認できません。'
        }
        if ($pluginName -eq 'agent-basics') {
            $skills = @(Get-ChildItem -LiteralPath (Join-Path $codexPluginCacheRoot "$expectedVersion/skills") -Directory -ErrorAction SilentlyContinue |
                Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'SKILL.md') -PathType Leaf })
            if ($skills.Count -eq 0) { throw 'Codex plugin更新後のskills実体を確認できません。' }
        }
    } finally {
        try {
            # 既存セッションが参照する全旧パスを関数の終了時に成立させるため、復旧は保護区間の終端で行う。
            Restore-CodexPluginCacheSnapshot $savedCacheRoot $expectedVersion $false
        } catch {
            throw "Codex plugin更新後の旧cache復旧に失敗しました。退避先を保持します: $savedCacheRoot。$($_.Exception.Message) $savedCacheRetry"
        }
    }
    Write-Output "Codex側の$pluginName プラグインを設定しました。"
}

function Install-CodexBasicsPlugin {
    Install-CodexPlugin agent-basics
}

function Get-ClaudePluginScriptPath {
    if (-not (Test-Path -LiteralPath $claudeInstalledPluginsPath -PathType Leaf)) { return }
    try {
        $data = Get-Content -LiteralPath $claudeInstalledPluginsPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "$claudeInstalledPluginsPath からClaude Code plugin一覧を取得できません。"
    }
    $pluginsProperty = $data.PSObject.Properties['plugins']
    if ($null -eq $pluginsProperty -or $null -eq $pluginsProperty.Value) {
        throw "$claudeInstalledPluginsPath のpluginsがJSONオブジェクトではありません。"
    }
    $entriesProperty = $pluginsProperty.Value.PSObject.Properties[$codexPluginId]
    if ($null -eq $entriesProperty) { return }
    foreach ($entry in @($entriesProperty.Value)) {
        if ($null -eq $entry) { continue }
        $installPathProperty = $entry.PSObject.Properties['installPath']
        if ($null -ne $installPathProperty -and $installPathProperty.Value -is [string]) {
            Join-Path $installPathProperty.Value 'agent_toolkit/agents_server_mcp.py'
        }
    }
}

function Initialize-AgentsServer {
    $scriptPaths = [System.Collections.Generic.List[string]]::new()
    try {
        foreach ($scriptPath in @(Get-ClaudePluginScriptPath)) { $scriptPaths.Add($scriptPath) }
    } catch {
        Write-Warning 'Claude Code plugin一覧からagents_serverのウォームアップ対象を解決できないためClaude Code分をスキップします。'
    }
    try {
        $expectedVersion = Get-CodexExpectedPluginVersion
        $scriptPaths.Add((Join-Path $codexPluginCacheRoot "$expectedVersion/agent_toolkit/agents_server_mcp.py"))
    } catch {
        Write-Warning 'Codex pluginからagents_serverのウォームアップ対象バージョンを解決できないためCodex分をスキップします。'
    }
    foreach ($scriptPath in @($scriptPaths | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
            Write-Warning "agents_serverのウォームアップ対象が存在しないためスキップします: $scriptPath"
            continue
        }
        $projectRoot = Split-Path (Split-Path $scriptPath -Parent) -Parent
        $null | & uv run --no-cache --no-config --no-project --python 3.15 python (Join-Path $projectRoot 'scripts/launch.py') --prepare $scriptPath > $null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "agents_serverのuv環境ウォームアップに失敗しました: $scriptPath"
            Write-Warning '次の操作: install-claude.ps1を再実行してください。'
            continue
        }
        $null | & uv run --no-cache --no-config --no-project --python 3.15 python (Join-Path $projectRoot 'scripts/launch.py') $scriptPath --check-dependencies > $null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "agents_serverの依存検査に失敗しました: $scriptPath"
            Write-Warning '次の操作: install-claude.ps1を再実行してください。'
        }
    }
}

function Write-CodexRestartNotice {
    [Console]::Error.WriteLine('Codex pluginを更新しました。実行中のCodexセッションを終了してから、次のコマンドでapp-server daemonを再起動してください。')
    [Console]::Error.WriteLine('codex app-server daemon restart')
}

function Test-CodexDaemonRunning {
    & codex app-server daemon version *> $null
    return $LASTEXITCODE -eq 0
}

function Get-LegacyUserCodexMcpStatus {
    $configPath = Join-Path $HOME '.claude.json'
    if (-not (Test-Path -LiteralPath $configPath)) { return 'missing' }

    try {
        $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "$configPath をJSONとして読み取れないため、Codex MCPの設定を変更しません。"
    }
    if ($null -eq $config -or $config.GetType() -ne [System.Management.Automation.PSCustomObject]) {
        throw "$configPath のルートがJSONオブジェクトではないため、Codex MCPの設定を変更しません。"
    }

    $mcpProperty = $config.PSObject.Properties['mcpServers']
    if ($null -eq $mcpProperty) { return 'missing' }
    if ($null -eq $mcpProperty.Value -or $mcpProperty.Value.GetType() -ne [System.Management.Automation.PSCustomObject]) {
        throw "$configPath のmcpServersがJSONオブジェクトではないため、Codex MCPの設定を変更しません。"
    }
    $codexProperty = $mcpProperty.Value.PSObject.Properties['codex']
    if ($null -eq $codexProperty -or $null -eq $codexProperty.Value) { return 'missing' }
    $definition = $codexProperty.Value
    $allowed = @('type', 'command', 'args', 'timeout', 'env')
    foreach ($property in $definition.PSObject.Properties) {
        if ($property.Name -notin $allowed) { return 'custom' }
    }
    $typeProperty = $definition.PSObject.Properties['type']
    if ($null -ne $typeProperty -and $null -ne $typeProperty.Value -and $typeProperty.Value -ne 'stdio') { return 'custom' }
    $commandProperty = $definition.PSObject.Properties['command']
    if ($null -eq $commandProperty -or $commandProperty.Value -ne 'codex') { return 'custom' }
    $argsProperty = $definition.PSObject.Properties['args']
    $argsValue = $null
    if ($null -ne $argsProperty) { $argsValue = $argsProperty.Value }
    if ($argsValue -isnot [System.Array] -or $argsValue.Count -ne 1 -or $argsValue[0] -isnot [string] -or $argsValue[0] -cne 'mcp-server') { return 'custom' }
    $timeoutProperty = $definition.PSObject.Properties['timeout']
    if ($null -ne $timeoutProperty -and $null -ne $timeoutProperty.Value) {
        $timeoutValue = $timeoutProperty.Value
        $numericTypes = @([byte], [sbyte], [int16], [uint16], [int32], [uint32], [int64], [uint64], [single], [double], [decimal])
        if (-not ($numericTypes | Where-Object { $_.IsInstanceOfType($timeoutValue) }) -or [decimal]$timeoutValue -ne 7200000) { return 'custom' }
    }
    # 旧installerが使う`claude mcp add`は`-e`未指定でもenvを空で書き込む。
    # 値を持つenvはユーザーが加えた設定として保持する。
    # 判定ケースは`pytools/_internal/legacy_codex_mcp_cases.json`にあり、bash版とpytoolsの実装も同じケースでテストする。
    $envProperty = $definition.PSObject.Properties['env']
    if ($null -ne $envProperty -and $null -ne $envProperty.Value) {
        $envValue = $envProperty.Value
        if ($envValue -isnot [System.Management.Automation.PSCustomObject]) { return 'custom' }
        if (@($envValue.PSObject.Properties).Count -ne 0) { return 'custom' }
    }
    return 'legacy'
}

# 判定できない設定では変更せずに失敗で終える（fail-closed）。dotfilesのpost-applyは同じ判定で何もせず続行するが、
# 単体インストーラーは導入手順の全体が1回の実行であり、ユーザーが失敗の内容から設定を直して再実行できるためである。
function Move-LegacyCodexMcp {
    $status = Get-LegacyUserCodexMcpStatus
    if ($status -eq 'missing') {
        return
    }
    if ($status -eq 'custom') {
        Write-Warning 'User scopeのcodex MCP定義はユーザー固有設定のため保持します。必要なら claude mcp remove --scope user codex を手動実行してください。'
        return
    }
    Invoke-RequiredNativeCommand claude @('mcp', 'remove', '--scope', 'user', 'codex')
    Write-Output '旧Codex MCPのUser scope登録を削除しました。'
}

# ~/.local/bin/atk.cmd へラッパーを配置する。
# インストール済み agent-toolkit プラグインの最新バージョンを動的解決するため、
# 参照先パス（cache/<marketplace>/agent-toolkit/<version>/scripts/atk.cmd）を実行時に決定する形とする。
# 直接コピーするとバージョン更新のたびに追随が必要となるため、実行時解決のラッパーを採用する。
function Install-AtkWrapper {
    $binDir = Join-Path $HOME '.local/bin'
    $wrapper = Join-Path $binDir 'atk.cmd'
    New-Item -ItemType Directory -Path $binDir -Force | Out-Null
    $body = @'
@echo off
setlocal enabledelayedexpansion
set "LATEST="
rem [version]型の自然順で最新実体を選択する。
for /f "usebackq delims=" %%A in (`powershell.exe -NoProfile -NonInteractive -Command "$root = Join-Path $env:USERPROFILE '.claude\plugins\cache'; Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue | ForEach-Object { $plugin = Join-Path $_.FullName 'agent-toolkit'; if (Test-Path -LiteralPath $plugin -PathType Container) { Get-ChildItem -LiteralPath $plugin -Directory | ForEach-Object { $candidate = Join-Path $_.FullName 'scripts\atk.cmd'; if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { $candidate = Join-Path $_.FullName 'bin\atk.cmd' }; if (Test-Path -LiteralPath $candidate -PathType Leaf) { [PSCustomObject]@{ Version = [version]$_.Name; Path = $candidate } } } } } | Sort-Object Version -Descending | Select-Object -First 1 -ExpandProperty Path"`) do (
    if not defined LATEST set "LATEST=%%A"
)
if not defined LATEST goto :not_found
if not exist "%LATEST%" goto :not_found
call "%LATEST%" %*
exit /b %ERRORLEVEL%
:not_found
echo atk: agent-toolkit プラグインが見つかりません。install-claude.ps1 を再実行してください。 1>&2
exit /b 1
'@
    Set-Content -LiteralPath $wrapper -Value $body -Encoding ASCII
    Write-Output "配置: $wrapper"
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not ($userPath -split ';' | Where-Object { $_ -ieq $binDir })) {
        Write-Warning "'$binDir' がユーザー PATH に含まれていません。setx PATH ""%PATH%;$binDir"" 等で追加してください。"
    }
}

function Uninstall-RetiredRule {
    foreach ($path in $retiredPaths) {
        # リンクやジャンクションはリンク先を辿らず、リンク自体を削除する。
        $item = Get-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
        if ($null -eq $item) { continue }
        if ($item.LinkType) {
            $item.Delete()
        } else {
            Remove-Item -LiteralPath $path -Recurse -Force
        }
        Write-Output "削除（旧い常時規範）: $path"
    }
}

function Main {
    param([switch]$BasicsOnly)
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8

    Assert-Command claude 'Claude Code' 'https://docs.anthropic.com/ja/docs/claude-code/overview'
    Assert-Command codex 'Codex' 'https://developers.openai.com/codex/cli/'
    if ($BasicsOnly) {
        Install-AgentBasicsPlugin
        Install-CodexBasicsPlugin
        Write-Output 'Claude Code・Codexのagent-basics設定が完了しました。'
        return
    }
    Assert-Command uv 'uv' 'https://docs.astral.sh/uv/getting-started/installation/'

    Install-AgentBasicsPlugin
    Install-CodexBasicsPlugin
    Install-AgentToolkitPlugin
    Install-CodexPlugin
    # Claude Code・Codexの両方へ新しい入口を導入できてから回収する。どちらかの更新に失敗した環境では旧版の規範を使い続けられる。
    Uninstall-RetiredRule
    Initialize-AgentsServer
    Move-LegacyCodexMcp
    Install-AtkWrapper
    Write-Output 'Claude Code・Codex、agent-toolkit、atkの設定が完了しました。'
}

$exitCode = 0
try {
    Main -BasicsOnly:$BasicsOnly
} catch {
    $exitCode = 1
    [Console]::Error.WriteLine($_.Exception.Message)
}
if ($script:codexPluginUpdated -and (Test-CodexDaemonRunning)) {
    Write-CodexRestartNotice
}
exit $exitCode
