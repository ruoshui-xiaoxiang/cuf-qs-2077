@echo off
setlocal
cd /d "%~dp0.."
py -3 tools\start_supervisor_console.py %*
