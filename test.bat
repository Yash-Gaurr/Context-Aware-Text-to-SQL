@echo off
cd /d "%~dp0"
echo Running Evaluation Suite (32 checks)...
text2sql\venv\Scripts\python.exe text2sql\tests\test_pipeline.py
pause
