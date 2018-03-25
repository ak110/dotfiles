@echo off

if "%XDG_CONFIG_HOME%"=="" (
    echo Error: %%XDG_CONFIG_HOME%% is not found
    pause
    exit 1 /B
)

if exist "%XDG_CONFIG_HOME%" rmdir "%XDG_CONFIG_HOME%"
mklink /D /J "%XDG_CONFIG_HOME%" "%~dp0.config"
pause
