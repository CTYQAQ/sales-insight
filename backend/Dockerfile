# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 依赖单独一层：改业务代码不会导致依赖重装
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

# SQLite 数据落到挂载卷里，容器销毁数据不丢
VOLUME ["/app/data"]

EXPOSE 8000

# 启动前跑一次校验：配置缺项会立刻报错，而不是等到第一个请求打进来才崩
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import httpx,sys; sys.exit(0 if httpx.get('http://127.0.0.1:8000/health',timeout=3).status_code==200 else 1)"

CMD ["python", "run.py"]
