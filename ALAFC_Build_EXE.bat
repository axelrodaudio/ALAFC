@echo off
rem  ALAFC - build standalone win64 .exe files (c) 2026 Axelrod. MIT License.
rem  Run this ONCE on Windows, in the C:\ALAFC folder. Takes a few minutes.
rem  Output: ALAFC_binaries\*.exe  - no Python needed to run them afterwards.
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo === ALAFC .exe builder ===
echo.

echo [1/9] Checking PyInstaller...
py -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo   Installing PyInstaller...
    py -m pip install pyinstaller --quiet
    if errorlevel 1 (
        echo   FAILED to install PyInstaller. Check your internet connection.
        pause
        exit /b 1
    )
) else (
    echo   Already installed.
)

if exist ALAFC_binaries rmdir /s /q ALAFC_binaries
mkdir ALAFC_binaries
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

set OK=1

echo.
echo [2/9] Building alafc.exe (core codec CLI: encode/decode/verify/info)...
py -m PyInstaller --onefile --console --name alafc ^
    --exclude-module numba --exclude-module llvmlite ^
    --hidden-import numpy ^
    alafc.py >build_alafc.log 2>&1
if exist dist\alafc.exe (
    copy /y dist\alafc.exe ALAFC_binaries\ >nul
    echo   OK
) else (
    echo   FAILED - see build_alafc.log
    set OK=0
)

echo.
echo [3/9] Building alafc_convert.exe (format converter)...
py -m PyInstaller --onefile --console --name alafc_convert ^
    --exclude-module numba --exclude-module llvmlite ^
    --hidden-import numpy ^
    alafc_convert.py >build_convert.log 2>&1
if exist dist\alafc_convert.exe (
    copy /y dist\alafc_convert.exe ALAFC_binaries\ >nul
    echo   OK
) else (
    echo   FAILED - see build_convert.log
    set OK=0
)

echo.
echo [4/9] Building alafc_player.exe (ASIO/WASAPI CLI player)...
py -m PyInstaller --onefile --console --name alafc_player ^
    --exclude-module numba --exclude-module llvmlite ^
    --hidden-import numpy --collect-all sounddevice ^
    alafc_player.py >build_player.log 2>&1
if exist dist\alafc_player.exe (
    copy /y dist\alafc_player.exe ALAFC_binaries\ >nul
    echo   OK
) else (
    echo   FAILED - see build_player.log
    set OK=0
)

echo.
echo [5/9] Building NeoAmp.exe (music player window)...
py -m PyInstaller --onefile --windowed --name NeoAmp ^
    --exclude-module numba --exclude-module llvmlite ^
    --hidden-import numpy --hidden-import tkinter ^
    --collect-all sounddevice ^
    neoamp.py >build_gui.log 2>&1
if exist dist\NeoAmp.exe (
    copy /y dist\NeoAmp.exe ALAFC_binaries\ >nul
    echo   OK
) else (
    echo   FAILED - see build_gui.log
    set OK=0
)

echo.
echo [6/9] Building TrueScope.exe (authenticity scope + player)...
py -m PyInstaller --onefile --windowed --name TrueScope ^
    --exclude-module numba --exclude-module llvmlite ^
    --hidden-import numpy --hidden-import tkinter ^
    --hidden-import scipy.signal --hidden-import scipy.ndimage ^
    --collect-submodules scipy.signal --collect-submodules scipy.ndimage ^
    --hidden-import matplotlib.backends.backend_tkagg ^
    --collect-all sounddevice ^
    truescope.py >build_truescope.log 2>&1
if exist dist\TrueScope.exe (
    copy /y dist\TrueScope.exe ALAFC_binaries\ >nul
    echo   OK
) else (
    echo   FAILED - see build_truescope.log
    set OK=0
)

echo.
echo [7/9] Building alafc_lossless_tester.exe (fake-lossless detector)...
py -m PyInstaller --onefile --console --name alafc_lossless_tester ^
    --exclude-module numba --exclude-module llvmlite ^
    --hidden-import numpy ^
    --hidden-import scipy.signal --hidden-import scipy.ndimage ^
    --collect-submodules scipy.signal --collect-submodules scipy.ndimage ^
    --hidden-import matplotlib.backends.backend_agg ^
    alafc_lossless_tester.py >build_tester.log 2>&1
if exist dist\alafc_lossless_tester.exe (
    copy /y dist\alafc_lossless_tester.exe ALAFC_binaries\ >nul
    echo   OK
) else (
    echo   FAILED - see build_tester.log
    set OK=0
)

echo.
echo [8/9] Quick smoke test (each exe should at least start and print usage)...
ALAFC_binaries\alafc.exe >nul 2>test_alafc.log
ALAFC_binaries\alafc_convert.exe >nul 2>test_convert.log
ALAFC_binaries\alafc_player.exe --list >nul 2>test_player.log
ALAFC_binaries\alafc_lossless_tester.exe >nul 2>test_tester.log
echo   Done (see test_*.log if something looks wrong; NeoAmp.exe and TrueScope.exe open windows - check them manually).

echo.
echo [9/9] Cleanup...
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
del /q *.spec 2>nul

echo.
if "%OK%"=="1" (
    echo ALL 5 EXE FILES BUILT: see the ALAFC_binaries folder.
) else (
    echo SOME BUILDS FAILED - check the build_*.log files listed above.
)
echo.
echo Note: PyInstaller .exe files are sometimes flagged by antivirus as a
echo false positive (common for small open-source PyInstaller tools with
echo no code-signing certificate). The source code is on GitHub if you want
echo to verify - that's the actual reference implementation.
echo.
pause

echo.
echo === Fast build (numba included - 30-60x quicker decode) ===
echo This needs numba installed. Installing/checking...
py -m pip install numba --quiet
if errorlevel 1 (
    echo   Could not install numba - skipping fast build. The exe files
    echo   above still work fine, just slower.
    pause
    exit /b 0
)

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo [1/6] alafc.exe (fast)...
py -m PyInstaller --onefile --console --name alafc ^
    --collect-all numba --collect-all llvmlite ^
    alafc.py >build_alafc_fast.log 2>&1
if exist dist\alafc.exe (copy /y dist\alafc.exe ALAFC_binaries\ >nul & echo   OK) else echo   FAILED - see build_alafc_fast.log

echo.
echo [2/6] alafc_convert.exe (fast)...
py -m PyInstaller --onefile --console --name alafc_convert ^
    --collect-all numba --collect-all llvmlite ^
    alafc_convert.py >build_convert_fast.log 2>&1
if exist dist\alafc_convert.exe (copy /y dist\alafc_convert.exe ALAFC_binaries\ >nul & echo   OK) else echo   FAILED - see build_convert_fast.log

echo.
echo [3/6] alafc_player.exe (fast)...
py -m PyInstaller --onefile --console --name alafc_player ^
    --collect-all numba --collect-all llvmlite --collect-all sounddevice ^
    alafc_player.py >build_player_fast.log 2>&1
if exist dist\alafc_player.exe (copy /y dist\alafc_player.exe ALAFC_binaries\ >nul & echo   OK) else echo   FAILED - see build_player_fast.log

echo.
echo [4/6] NeoAmp.exe (fast)...
py -m PyInstaller --onefile --windowed --name NeoAmp ^
    --collect-all numba --collect-all llvmlite --collect-all sounddevice ^
    --hidden-import tkinter ^
    neoamp.py >build_gui_fast.log 2>&1
if exist dist\NeoAmp.exe (copy /y dist\NeoAmp.exe ALAFC_binaries\ >nul & echo   OK) else echo   FAILED - see build_gui_fast.log

echo.
echo [5/6] TrueScope.exe (fast)...
py -m PyInstaller --onefile --windowed --name TrueScope ^
    --collect-all numba --collect-all llvmlite --collect-all sounddevice ^
    --hidden-import tkinter ^
    --hidden-import scipy.signal --hidden-import scipy.ndimage ^
    --collect-submodules scipy.signal --collect-submodules scipy.ndimage ^
    --hidden-import matplotlib.backends.backend_tkagg ^
    truescope.py >build_truescope_fast.log 2>&1
if exist dist\TrueScope.exe (copy /y dist\TrueScope.exe ALAFC_binaries\ >nul & echo   OK) else echo   FAILED - see build_truescope_fast.log

echo.
echo [6/6] alafc_lossless_tester.exe (fast - no numba use here, rebuilt for consistency)...
py -m PyInstaller --onefile --console --name alafc_lossless_tester ^
    --hidden-import scipy.signal --hidden-import scipy.ndimage ^
    --collect-submodules scipy.signal --collect-submodules scipy.ndimage ^
    --hidden-import matplotlib.backends.backend_agg ^
    alafc_lossless_tester.py >build_tester_fast.log 2>&1
if exist dist\alafc_lossless_tester.exe (copy /y dist\alafc_lossless_tester.exe ALAFC_binaries\ >nul & echo   OK) else echo   FAILED - see build_tester_fast.log

rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
del /q *.spec 2>nul

echo.
echo Fast build done - ALAFC_binaries now has the numba-accelerated exe files
echo (30-60x faster decode). If any "FAILED" appeared above, that specific
echo exe was NOT replaced - the safe no-numba version from before still
echo works fine, just slower.
echo.
pause
