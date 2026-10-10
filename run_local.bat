@echo off
set APP_ENV=local
if "%APP_MODE%"=="" set APP_MODE=demo
py -3 -m pip install -r requirements.txt
py -3 -m uvicorn webapp:app --host 127.0.0.1 --port 8000 --workers 1
