FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1
WORKDIR /build

RUN python -m pip install --upgrade "pip>=26.1.2" "build>=1.2,<2"

COPY pyproject.toml README.md ./
COPY work_hunter ./work_hunter
RUN python -m build --wheel --outdir /dist

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    QT_QUICK_BACKEND=software \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /dist/work_hunter-1.0.0-py3-none-any.whl /tmp/
RUN python -m pip install --upgrade "pip>=26.1.2" \
    && python -m pip install /tmp/work_hunter-1.0.0-py3-none-any.whl \
    && rm /tmp/work_hunter-1.0.0-py3-none-any.whl

ARG INSTALL_PLAYWRIGHT=false
RUN if [ "$INSTALL_PLAYWRIGHT" = "true" ]; then \
        python -m pip install "beautifulsoup4>=4.12,<5" "playwright>=1.45,<2" \
        && python -m playwright install --with-deps chromium \
        && chmod -R a+rX /ms-playwright \
        && rm -rf /var/lib/apt/lists/*; \
    fi

RUN groupadd --gid 10001 workhunter \
    && useradd --uid 10001 --gid 10001 --create-home workhunter \
    && mkdir -p /data \
    && chown -R 10001:10001 /data

WORKDIR /data
USER 10001:10001
VOLUME ["/data"]
ENTRYPOINT ["/usr/bin/tini", "--", "work-hunter", "--root", "/data"]
CMD ["hh-auth-status"]
