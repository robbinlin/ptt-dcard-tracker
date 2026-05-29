@echo off
chcp 65001 >nul
echo ============================================
echo  PTT / Dcard Tracker - Setup
echo ============================================

:: 建立虛擬環境
python -m venv venv
call venv\Scripts\activate

:: 安裝相依套件
pip install --upgrade pip
pip install -r requirements.txt

:: 複製設定檔
if not exist .env (
    copy .env.example .env
    echo [OK] .env created. Please edit keywords in .env if needed.
)

echo.
echo ============================================
echo  Done! Start the server with:
echo    start.bat
echo  API Docs: http://localhost:8000/docs
echo ============================================
pause
