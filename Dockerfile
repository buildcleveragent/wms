FROM python:3.12.15-slim-trixie@sha256:02108f5d322dd89f1c9e552442c25acb0543dfdbc455693a5599624f20d9155d AS base
ARG DEBIAN_MIRROR=https://deb.debian.org
RUN sed -i "s|http://deb.debian.org/debian$|${DEBIAN_MIRROR}/debian|" /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 update \
    && apt-get upgrade -y --no-install-recommends

FROM base AS builder

RUN apt-get install -y --no-install-recommends \
    build-essential default-libmysqlclient-dev pkg-config libffi-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build
COPY requirements/prod.txt requirements/prod.txt
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip wheel --index-url "$PIP_INDEX_URL" --require-hashes --no-cache-dir --wheel-dir /wheels -r requirements/prod.txt

FROM base AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
RUN apt-get install -y --no-install-recommends \
    libmariadb3 libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b \
    libjpeg62-turbo libopenjp2-7 libffi8 fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && python -m venv /opt/venv \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin wms
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/* \
    && rm -rf /wheels

WORKDIR /app
COPY --chown=wms:wms . /app
RUN chmod +x /app/docker/start-web.sh /app/docker/start-scheduler.sh /app/docker/release.sh
RUN install -d -o 10001 -g 10001 -m 0755 /app/staticfiles /app/media
USER wms
EXPOSE 8000
CMD ["/app/docker/start-web.sh"]
