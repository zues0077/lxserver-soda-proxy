@echo off
chcp 65001 >nul
title soda-206 screen
echo Connecting to soda container (192.168.1.206:5555) ...
adb connect 192.168.1.206:5555
echo Launching scrcpy window ...
start "" "E:\AI\tools\scrcpy-win64-v5.0.1\scrcpy.exe" -s 192.168.1.206:5555 --no-audio --stay-awake --window-title "soda-206"
echo.
echo If a window opened, operate the phone in it directly.
echo Close this console window when done.
timeout /t 5 >nul
