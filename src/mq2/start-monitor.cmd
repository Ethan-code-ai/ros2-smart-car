@echo off
chcp 65001 >nul
title MQ2 Live Readings
cd /d "%~dp0"
python monitor.py %*
pause
