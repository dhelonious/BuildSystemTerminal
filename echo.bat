@echo off
set "args=%*"
if not "%args%"=="" (
    set "args=%args:\x=0x%"
    forfiles /p "%~dp0." /m "%~nx0" /c "cmd /c echo(%args%"
) else (
    echo(
)
