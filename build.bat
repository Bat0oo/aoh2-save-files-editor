@echo off
REM ============================================
REM  AoH2 Save Editor - build standalone .exe
REM ============================================

echo Installing dependencies...
pip install javaobj-py3 pyinstaller
if errorlevel 1 (
    echo.
    echo ERROR: pip install failed. Is Python installed and on PATH?
    pause
    exit /b 1
)

echo.
echo Building AoH2SaveEditor.exe ...
pyinstaller --onefile --windowed --name AoH2SaveEditor aoh2_editor.py
if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller build failed. See output above.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Done! Your exe is at:  dist\AoH2SaveEditor.exe
echo ============================================
pause
