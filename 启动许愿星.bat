@echo off
chcp 65001 >nul
title Superposition - 许愿星 / 星星瓶 API
cd /d D:\superposition
echo 正在启动许愿星后端（本机 127.0.0.1:8321，关闭本窗口即停止服务）...
echo 如需公网访问：自行配置反向代理 / Cloudflare 隧道（见 README 第 7 节）。
.venv\Scripts\python.exe run_api.py
pause
