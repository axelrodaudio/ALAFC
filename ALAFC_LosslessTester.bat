@echo off
rem  ALAFC Lossless Tester - drag & drop (c) 2026 Axelrod. MIT License.
rem  Drop audio files (wav/flac/mp3/alafc/...) onto this icon.
rem  For each: prints a verdict + saves a spectrogram PNG next to the file.
cd /d "%~dp0"
if "%~1"=="" (
  echo Drop audio files onto this icon to check them for a fake-lossless
  echo (transcoded from lossy) frequency cutoff.
  pause
  exit /b
)
py alafc_lossless_tester.py %*
echo.
echo Отвори .png файловете за да видиш спектрограмите.
pause
