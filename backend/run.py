"""启动脚本：python run.py

本机开发推荐这条命令，等价于 uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
"""
import sys

import uvicorn

from app.core.config import settings

if __name__ == "__main__":
    try:
        settings.validate_startup()
    except RuntimeError as exc:
        print(f"[配置错误] {exc}")
        print("请把 backend/.env.example 复制为 backend/.env 并填写 DEEPSEEK_API_KEY")
        sys.exit(2)

    uvicorn.run(
        "app.main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=settings.APP_ENV == "dev",
        log_level="info",
    )
