@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================
echo   Meridian - 外贸客户工作台
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [x] 没找到 python。请先安装 Python 3.11 或更新版本，安装时勾选 "Add to PATH"。
  echo     下载： https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] 正在创建虚拟环境（只需要一次）...
  python -m venv .venv
  if errorlevel 1 (
    echo [x] 创建虚拟环境失败。
    pause
    exit /b 1
  )
) else (
  echo [1/3] 虚拟环境已存在，跳过。
)

echo [2/3] 检查依赖...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo [x] 依赖安装失败，检查网络或代理。
  pause
  exit /b 1
)

if not exist ".env" (
  echo.
  echo [!] 还没有 .env。已从 .env.example 复制一份给你。
  copy ".env.example" ".env" >nul
  echo     现在打开记事本，把 DATABASE_URL 和 SESSION_SECRET 填上，
  echo     保存后再重新双击本文件。
  echo.
  notepad ".env"
  pause
  exit /b 0
)

echo [3/3] 启动服务...
echo.
echo     浏览器会自动打开。关掉这个窗口 = 停止服务。
echo     首次使用：注册 -^> 登录 -^> 到「智能体」页填自己的模型 key。
echo.
start "" "http://127.0.0.1:8000"
".venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000

echo.
echo 服务已停止。
pause
