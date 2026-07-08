@echo off
REM ============================================================================
REM  Talkloom - PyInstaller build script (single-line command: no ^ carets,
REM  which break if line endings or trailing spaces get mangled)
REM  Output: dist\Talkloom.exe  (~200-400 MB is healthy; KB means it failed)
REM ============================================================================

pyinstaller --noconfirm --onefile --windowed --name Talkloom --icon talkloom.ico --collect-all faster_whisper --collect-all ctranslate2 --collect-all onnxruntime --collect-all certifi --hidden-import PySide6.QtCore --hidden-import PySide6.QtGui --hidden-import PySide6.QtWidgets --add-data "silero_vad.onnx;." --add-data "talkloom.ico;." --hidden-import sounddevice --hidden-import pynput.keyboard._win32 --hidden-import pynput.mouse._win32 --hidden-import pyperclip --hidden-import truststore --exclude-module tkinter --exclude-module matplotlib --exclude-module torch --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick --exclude-module PySide6.QtQuick3D --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets --exclude-module PySide6.QtWebEngineQuick --exclude-module PySide6.QtMultimedia --exclude-module PySide6.QtMultimediaWidgets --exclude-module PySide6.QtPdf --exclude-module PySide6.QtPdfWidgets --exclude-module PySide6.QtDesigner --exclude-module PySide6.QtCharts --exclude-module PySide6.QtDataVisualization --exclude-module PySide6.QtBluetooth --exclude-module PySide6.QtPositioning --exclude-module PySide6.QtSerialPort --exclude-module PySide6.QtSql --exclude-module PySide6.QtTest --exclude-module PySide6.QtSensors --exclude-module PySide6.Qt3DCore --exclude-module PySide6.Qt3DRender --exclude-module PySide6.QtRemoteObjects main.py

echo.
echo Build complete: dist\Talkloom.exe
pause
