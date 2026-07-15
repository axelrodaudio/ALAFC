@echo off
rem  ALAFC drag & drop converter (c) 2026 Axelrod
rem  Drop audio files onto this icon:
rem    song.flac / song.mp3 / song.wav  ->  song.alafc
rem    song.alafc                       ->  song.wav
cd /d "%~dp0"
if "%~1"=="" (
  echo Drop an audio file onto this icon to convert it.
  echo   .flac / .mp3 / .wav  -^>  .alafc
  echo   .alafc               -^>  .wav
  pause
  exit /b
)
:loop
if "%~1"=="" goto done
echo.
echo ================ %~nx1 ================
py alafc_convert.py "%~1"
if errorlevel 1 echo !!! Problem with this file - see message above.
shift
goto loop
:done
echo.
echo Done. New files are next to the originals.
pause
