# 使用官方的 Python 作为基础镜像
# FROM python:3.11-slim
FROM m.daocloud.io/docker.io/library/python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖（用于编译一些 Python 包）
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 将本地的应用代码复制到容器中
COPY . /app/

# 创建并激活虚拟环境
RUN python3 -m venv /env
ENV PATH="/env/bin:$PATH"

# 安装项目依赖
RUN pip install --no-cache-dir -r requirements.txt

# 设置 Django 所需的环境变量
ENV PYTHONUNBUFFERED=1

RUN chmod +x /app/docker/start.sh

# 设置容器启动时执行的命令
CMD ["/app/docker/start.sh"]
