"""启动开发服务器：python run_api.py（或 .venv/Scripts/python run_api.py）
端口默认 8321，可用环境变量 SUPERPOSITION_PORT 覆盖。"""
import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("SUPERPOSITION_PORT", "8321"))
    uvicorn.run("app.main:app", host="127.0.0.1", port=port)
