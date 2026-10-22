@echo off
cd /d "%~dp0"
echo Starting Context-Aware Text-to-SQL Assistant...
echo Opening in default browser at http://localhost:5000 ...
start http://localhost:5000
text2sql\venv\Scripts\python.exe run.py
pause
