@echo off
REM ============================================================================
REM  EchoScribe - PyInstaller build script (single-line command: no ^ carets,
REM  which break if line endings or trailing spaces get mangled)
REM  Output: dist\EchoScribe.exe  (~200-400 MB is healthy; KB means it failed)
REM ============================================================================

pyinstaller --noconfirm --onefile --windowed --name EchoScribe --icon echoscribe.ico --collect-all faster_whisper --collect-all ctranslate2 --collect-all onnxruntime --collect-all certifi --collect-all PySide6 --add-data "silero_vad.onnx;." --add-data "echoscribe.ico;." --hidden-import sounddevice --hidden-import pynput.keyboard._win32 --hidden-import pynput.mouse._win32 --hidden-import pyperclip --hidden-import truststore --exclude-module tkinter --exclude-module matplotlib --exclude-module torch main.py

echo.
echo Build complete: dist\EchoScribe.exe
pause
