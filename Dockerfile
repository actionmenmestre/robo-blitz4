FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY engine_v15.py worker.py ./
CMD ["python", "worker.py"]
