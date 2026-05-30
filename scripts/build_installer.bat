@echo off
REM Borsa Bot — Tek tıkla installer build
REM 1) PyInstaller ile dist\BorsaBot\BorsaBot.exe (onedir)
REM 2) Inno Setup ile installer_output\BorsaBot-Setup-X.Y.Z.exe

setlocal
cd /d "%~dp0\.."

set PYEXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe
set ISCC=%PROGRAMFILES(X86)%\Inno Setup 6\ISCC.exe
if not exist "%ISCC%" set ISCC=%PROGRAMFILES%\Inno Setup 6\ISCC.exe

echo ==============================
echo  Adim 1/3: Eski build'leri temizle
echo ==============================
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist installer_output rmdir /s /q installer_output
if exist BorsaBot.spec del /q BorsaBot.spec

echo.
echo ==============================
echo  Adim 2/3: PyInstaller (onedir)
echo ==============================
"%PYEXE%" scripts\build_exe.py
if errorlevel 1 (
    echo HATA: PyInstaller basarisiz.
    exit /b 1
)

echo.
echo ==============================
echo  Adim 3/3: Inno Setup (installer)
echo ==============================
if not exist "%ISCC%" (
    echo HATA: Inno Setup bulunamadi. winget install JRSoftware.InnoSetup ile kurabilirsiniz.
    exit /b 1
)
"%ISCC%" installer.iss
if errorlevel 1 (
    echo HATA: Inno Setup basarisiz.
    exit /b 1
)

echo.
echo ==============================
echo  TAMAM
echo ==============================
echo.
echo Cikti: installer_output\BorsaBot-Setup-*.exe
dir installer_output\*.exe
