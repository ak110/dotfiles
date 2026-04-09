@{
    # PSScriptAnalyzer 設定。
    # executable_run-psscriptanalyzer から -Settings 経由で読み込まれる。
    ExcludeRules = @(
        # install.ps1 は対話的なインストーラであり、進行状況をユーザーに
        # 直接表示する用途で Write-Host を使う。Write-Output だと
        # パイプライン出力になり意味が変わるため除外する。
        'PSAvoidUsingWriteHost'
    )
}
