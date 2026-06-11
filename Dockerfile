# ============================================================
# CheckInHelper — Dockerfile
# 多阶段构建 (Python 3.13.13 slim)
# ============================================================
FROM python:3.13.13-slim AS builder

WORKDIR /app

# 安装 uv 包管理器
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 先拷贝依赖清单，利用 Docker 层缓存
COPY pyproject.toml ./
RUN uv sync --no-dev --frozen 2>/dev/null || uv sync --no-dev

# ============================================================
FROM python:3.13.13-slim AS runtime

WORKDIR /app

# 运行时系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 从 builder 拷贝虚拟环境
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# 拷贝应用代码
COPY . .

# 静态资源 + 前端模板
EXPOSE 8765

# 启动（自动加载 .env）
CMD ["python", "main.py"]
