@echo off
rem NOTE: Linux side -> bin/_claude-wrapper.sh
rem Shared launcher for sonnet.cmd / opus.cmd / fable.cmd.
rem Clears the screen only when the session is interactive and exits normally,
rem so that diagnostics on failure stay visible.
setlocal
set "INTERACTIVE=1"
for %%A in (%*) do (
    if /I "%%~A"=="--help" set "INTERACTIVE=0"
    if /I "%%~A"=="-h" set "INTERACTIVE=0"
    if /I "%%~A"=="--version" set "INTERACTIVE=0"
    if /I "%%~A"=="-v" set "INTERACTIVE=0"
    if /I "%%~A"=="--print" set "INTERACTIVE=0"
    if /I "%%~A"=="-p" set "INTERACTIVE=0"
)
call claude %*
set "RC=%ERRORLEVEL%"
if "%INTERACTIVE%"=="1" if "%RC%"=="0" call "%~dp0c.cmd"
endlocal & exit /b %RC%
