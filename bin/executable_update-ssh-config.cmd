@echo off
setlocal
REM conf.d配下の設定ファイルを結合してSSH設定を生成
type "%USERPROFILE%\.ssh\conf.d\*.conf" > "%USERPROFILE%\.ssh\config" 2>nul
if exist "%USERPROFILE%\.ssh\localconfig" (
    type "%USERPROFILE%\.ssh\localconfig" >> "%USERPROFILE%\.ssh\config"
)
