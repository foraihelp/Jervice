@echo off
REM Builds a standalone Jarvis.exe using PyInstaller.
REM Run this from an activated venv that already has requirements.txt AND
REM requirements-build.txt installed (see BUILD_EXE.md for full steps).

echo Building Jarvis.exe with PyInstaller...
echo This can take several minutes and the venv must already have all
echo dependencies installed (pip install -r requirements.txt -r requirements-build.txt).
echo.

pyinstaller ^
  --name Jarvis ^
  --onedir ^
  --console ^
  --noconfirm ^
  --collect-all openwakeword ^
  --collect-all faster_whisper ^
  --collect-all ctranslate2 ^
  --collect-all pystray ^
  --collect-all PIL ^
  --collect-all webview ^
  --collect-all openai ^
  --hidden-import pyttsx3.drivers ^
  --hidden-import pyttsx3.drivers.sapi5 ^
  --hidden-import win32timezone ^
  --add-data "jarvis\ui\assets;jarvis\ui\assets" ^
  main.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo BUILD FAILED. Scroll up for the actual error. See BUILD_EXE.md
    echo "Troubleshooting" section for common fixes.
    exit /b 1
)

echo.
echo Copying config.yaml next to the built exe (used as the read-only
echo template the app copies from into %%LOCALAPPDATA%%\Jarvis\config.yaml
echo on first run -- see jarvis/config.py)...
copy /Y config.yaml dist\Jarvis\config.yaml
copy /Y .env.example dist\Jarvis\.env.example

echo.
echo NOTE: this build does NOT need a .env next to the exe. The app creates
echo its own .env under %%LOCALAPPDATA%%\Jarvis\.env on first launch, and
echo prompts for your AI provider's API key right in its Settings window
echo (also lets you switch between Claude, OpenAI, or any OpenAI-compatible
echo endpoint) -- this also keeps working correctly if Jarvis.exe ends up
echo somewhere non-writable like Program Files (see installer/README.md).

echo.
echo Writing run_jarvis.bat wrapper -- keeps the window open if Jarvis.exe
echo crashes, so double-clicking doesn't just flash a window and vanish.
(
  echo @echo off
  echo cd /d "%%~dp0"
  echo Jarvis.exe
  echo echo.
  echo echo Jarvis has exited. If that was unexpected ^(a crash^), scroll up
  echo echo to read the error above.
  echo pause
) > dist\Jarvis\run_jarvis.bat

echo.
echo Done. Your app is in: dist\Jarvis\
echo Run it with: dist\Jarvis\run_jarvis.bat  (recommended -- stays open on crash)
echo Or directly:  dist\Jarvis\Jarvis.exe      (fine once you know it's working)
