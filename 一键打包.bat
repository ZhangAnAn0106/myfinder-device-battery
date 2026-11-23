@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "OUT=%~dp0打包输出\pkg-%RANDOM%%RANDOM%"
echo 正在打包：先编译主程序，再准备设备读取组件，最后生成完整 ZIP。
echo 第一次打包要下载便携 Python 运行库，会慢一点，请稍等...
echo.
set "MYFINDER_SKIP_REGRESSION=1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\Package.ps1" -OutputDirectory "%OUT%"
if errorlevel 1 (
    echo.
    echo 打包失败：看上面最后的报错，截图发我。
    pause
    exit /b 1
)
echo.
echo 打包完成，ZIP 在这个文件夹里：
echo %OUT%
start "" "%OUT%"
pause
