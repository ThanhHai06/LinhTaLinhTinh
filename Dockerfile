FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_ENV=production PORT=8000
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scanner.py rewards.py store.py realtime.py webcore.py webapp.py entrypoint.py ./
COPY static ./static
RUN useradd -r -u 10001 app && mkdir -p /data && chown -R app:app /data /app
ENV DATA_DIR=/data
EXPOSE 8000
CMD ["python", "entrypoint.py"]
