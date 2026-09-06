@echo off 
:loop 
curl -s -o nul http://127.0.0.1:8000 
if errorlevel 1 ( 
    ping -n 2 127.0.0.1 >nul 
    goto loop 
) 
start http://127.0.0.1:8000 
