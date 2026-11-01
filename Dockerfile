FROM python:3.12-slim AS dependencies
WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DB_PATH=/data/smartgas.db TANK_CONFIG_PATH=/config/tanks.json
RUN groupadd --gid 10001 smartgas && useradd --uid 10001 --gid 10001 --no-create-home smartgas \
    && mkdir -p /app /data /config && chown 10001:10001 /data
COPY --from=dependencies /wheels /wheels
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r /tmp/requirements.txt \
    && rm -rf /wheels /tmp/requirements.txt
WORKDIR /app
COPY --chown=10001:10001 app ./app
COPY config/tanks.json /config/tanks.json
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/live',timeout=3)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-server-header"]
