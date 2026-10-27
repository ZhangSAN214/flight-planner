@echo off
chcp 65001 >nul
cd /d %~dp0
if not exist .venv (
  echo 第一次运行：创建虚拟环境并安装依赖...
  python -m venv .venv || (echo 请先安装 Python 3.11 或更高版本 & pause & exit /b 1)
  .venv\Scripts\python -m pip install -q -e .
)
if not exist .env copy .env.example .env >nul
.venv\Scripts\flightplanner serve
pause
