FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

ENV CALIBRE_LIBRARY_PATH=/calibre-library
ENV MCP_PORT=8100
ENV LOG_LEVEL=INFO

EXPOSE 8100

CMD ["python", "server.py"]
