@echo off
REM Run the editor directly from source (no build needed)
pip show javaobj-py3 >nul 2>&1 || pip install javaobj-py3
python aoh2_editor.py
if errorlevel 1 pause
