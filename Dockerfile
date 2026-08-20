FROM python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2 AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential default-libmysqlclient-dev pkg-config libffi-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY requirements/ requirements/
RUN pip wheel --require-hashes --no-cache-dir --wheel-dir /wheels -r requirements/prod.txt

FROM python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmariadb3 libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b \
    libjpeg62-turbo libopenjp2-7 libffi8 shared-mime-info \
    && rm -rf /var/lib/apt/lists/* \
    && python -m venv /opt/venv \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin wms
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/* \
    && rm -rf /wheels

WORKDIR /app
COPY --chown=wms:wms . /app
RUN chmod +x /app/docker/start-web.sh /app/docker/start-scheduler.sh /app/docker/release.sh
USER wms
EXPOSE 8000
CMD ["/app/docker/start-web.sh"]
