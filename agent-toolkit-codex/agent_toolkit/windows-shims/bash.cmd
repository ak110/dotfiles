@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "GIT_EXEC_PATH="
for /f "delims=" %%P in ('git.exe --exec-path 2^>nul') do if not defined GIT_EXEC_PATH set "GIT_EXEC_PATH=%%P"
if defined GIT_EXEC_PATH goto git_found
>&2 echo Git for Windows‚ÌŽÀsêŠ‚ð‰ðŒˆ‚Å‚«‚Ü‚¹‚ñB
exit /b 127

:git_found
for %%P in ("%GIT_EXEC_PATH%\..\..\..\bin\bash.exe") do set "GIT_BASH=%%~fP"
if exist "%GIT_BASH%" goto bash_found
>&2 echo Git for Windows‚Ìbash.exe‚ð‰ðŒˆ‚Å‚«‚Ü‚¹‚ñ: %GIT_BASH%
exit /b 127

:bash_found
set "FIRST_ARG=%~1"
if not "%FIRST_ARG:~1,2%"==":\" goto passthrough
"%GIT_BASH%" -c "script=$(cygpath -u -- \"$1\") || exit 127; shift; exec \"$script\" \"$@\"" bash %*
exit /b %ERRORLEVEL%

:passthrough
"%GIT_BASH%" %*
exit /b %ERRORLEVEL%
