function Write-UpdateError {
    param([string]$Message)
    [UvOutputRecord]::PrintError($Message)
}
# Jobのreaderからも呼ぶ。PowerShellのパイプラインがCtrl+Cで停止しても、EOFまで読んでから記録を閉じる。
if (-not ('UvOutputRecord' -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Collections.Generic;
public sealed class UvOutputRecord : TextWriter {
    public readonly List<string> Saved = new List<string>(), Hidden = new List<string>();
    public string Summary, Cause, MatchText;
    public bool PrivateKey, Matched;
    readonly Regex sensitive, actionable, routine;
    bool continuation;
    public UvOutputRecord(string[] rules) {
        if (rules.Length < 3) return;
        sensitive = new Regex(rules[0], RegexOptions.IgnoreCase);
        actionable = new Regex(rules[1]);
        routine = new Regex("^(" + String.Join("|", rules, 2, rules.Length-2) + ")$");
    }
    public override Encoding Encoding { get { return Encoding.UTF8; } }
    public override void WriteLine(string line) {
        if (MatchText != null && line.Contains(MatchText)) Matched = true;
        bool visible = true;
        if (routine != null) {
            PrivateKey |= line.Contains("PRIVATE KEY-----");
            Saved.Add(sensitive.IsMatch(line) ? "[REDACTED]" : line);
            bool ordinary = line.Length == 0 || routine.IsMatch(line);
            visible = !ordinary && (actionable.IsMatch(line) || (continuation && line[0]==' '));
            continuation = visible;
            if (!visible) {
                Hidden.Add(line);
                if (!ordinary) {
                    Summary = line;
                    if (line.Contains("cause:") || line.StartsWith("error:")) Cause = line;
                }
            }
        }
        if (visible) PrintError(line);
    }
    public static void PrintError(string message) {
        if (!Console.IsErrorRedirected) { Console.Error.WriteLine(message); return; }
        using (var writer = new StreamWriter(Console.OpenStandardError(), new UTF8Encoding(false))) writer.WriteLine(message);
    }
}
'@
}
function New-UvOutput {
    [Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSUseShouldProcessForStateChangingFunctions', '', Justification = 'メモリ上の記録を作るだけで、ファイルは完了時に発行する。')]
    param()
    [string[]]$rules = @()
    try {
        $rules = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'uv_output_patterns.txt') -Encoding UTF8 -ErrorAction Stop
        return [UvOutputRecord]::new($rules)
    } catch {
        Write-UpdateError "明細を分類できません: $_。更新ファイルを確認してください。"
        return [UvOutputRecord]::new([string[]]@())
    }
}
function Complete-UvOutput {
    param([string]$Stage, [UvOutputRecord]$Record, [int]$Code)
    if (-not $Record.Saved.Count -and $Code -eq 0) { return }
    $temp = $null
    $log = $null
    try {
        if ($Code -ne 0) { $stage += '-failed' }
        $directory = Join-Path $env:LOCALAPPDATA 'agent-toolkit'
        $log = Join-Path $directory "uv-$stage.log"
        if ($Record.PrivateKey) { $Record.Saved.Clear(); $Record.Saved.Add('[REDACTED]') }
        $Record.Saved.Insert(0, "$(Get-Date -Format o) exit=$Code")
        [void][IO.Directory]::CreateDirectory($directory)
        $temp = Join-Path $directory ([IO.Path]::GetRandomFileName())
        [IO.File]::WriteAllLines($temp, $Record.Saved, [Text.UTF8Encoding]::new($false))
        if ([IO.File]::Exists($log)) { [IO.File]::Replace($temp, $log, [NullString]::Value) }
        else { [IO.File]::Move($temp, $log) }
    } catch {
        Write-UpdateError ("明細を保存できません: ${log}: $_。書込権限と空き容量を確認してください。")
        foreach ($line in $Record.Hidden) { Write-UpdateError $line }
        return
    } finally {
        if ($temp) { Remove-Item -LiteralPath $temp -ErrorAction SilentlyContinue }
    }
    if ($Code -ne 0) {
        $summary = if ($Record.Cause) { $Record.Cause } elseif ($Record.Summary) { $Record.Summary } else { "exit=$Code" }
        Write-UpdateError ("$summary (詳細: $log)")
    }
}
function Write-UvOutput {
    param([string]$Stage, [int]$Code, [string]$OutputText)
    $record = New-UvOutput
    $record.WriteLine($OutputText)
    Complete-UvOutput $Stage $record $Code
}
