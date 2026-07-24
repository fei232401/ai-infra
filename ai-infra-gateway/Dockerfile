FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
COPY 01-gateway-server/gateway_server.py .
COPY 01-gateway-server/config/ ./config/
EXPOSE 8000
CMD ["python", "gateway_server.py"]
