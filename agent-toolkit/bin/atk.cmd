@echo off
setlocal
for /f "delims=" %%A in ('cd /d "%~dp0.." ^& cd') do set "SCRIPT_DIR=%%A"
set "SCRIPT=%SCRIPT_DIR%\scripts\atk.py"

rem Run non-daemon subcommands once, preserving the existing behavior.
if not "%~1"=="mq" goto :run_once
if not "%~2"=="process-loop" goto :run_once

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
  "& { param([string]$InitialScript) $script = $InitialScript; $arguments = @($args); $spec = [IO.Path]::GetTempFileName(); $env:AGENT_TOOLKIT_RESTART_SPEC = $spec; $exitCode = 0; try { while ($true) { [IO.File]::WriteAllText($spec, ''); & uv run --no-project --script $script @arguments; $status = $LASTEXITCODE; if ($status -ne 75) { $exitCode = $status; break }; $next = @(Get-Content -LiteralPath $spec -Encoding UTF8); if ($next.Count -lt 1 -or [string]::IsNullOrEmpty($next[0])) { $exitCode = $status; break }; $script = $next[0]; if ($next.Count -gt 1) { $arguments = @($next[1..($next.Count - 1)]) } else { $arguments = @() } } } finally { Remove-Item -LiteralPath $spec -Force -ErrorAction SilentlyContinue }; exit $exitCode }" ^
  "%SCRIPT%" %*
set "STATUS=%ERRORLEVEL%"
goto :finish

:run_once
uv run --no-project --script "%SCRIPT%" %*
set "STATUS=%ERRORLEVEL%"

:finish
endlocal & exit /b %STATUS%
