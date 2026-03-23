@echo off
setlocal

REM SSH config: conf.d\*.conf + localconfig → config
type "%USERPROFILE%\.ssh\conf.d\*.conf" > "%USERPROFILE%\.ssh\config" 2>nul
if exist "%USERPROFILE%\.ssh\localconfig" (
    type "%USERPROFILE%\.ssh\localconfig" >> "%USERPROFILE%\.ssh\config"
)

REM authorized_keys: conf.d\authorized_keys + local_authorized_keys → authorized_keys
copy /y "%USERPROFILE%\.ssh\conf.d\authorized_keys" "%USERPROFILE%\.ssh\authorized_keys" >nul
if exist "%USERPROFILE%\.ssh\local_authorized_keys" (
    type "%USERPROFILE%\.ssh\local_authorized_keys" >> "%USERPROFILE%\.ssh\authorized_keys"
)
