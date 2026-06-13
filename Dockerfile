FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    QT_QUICK_BACKEND=software \
    WORK_HUNTER_ROOT=/data

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        dos2unix \
        tini \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --upgrade pip \
    && pip install "mcp>=1.0" "starlette>=0.27,<0.47"

ARG INSTALL_PLAYWRIGHT=false
RUN if [ "$INSTALL_PLAYWRIGHT" = "true" ]; then \
        pip install playwright && python -m playwright install --with-deps chromium; \
    fi

COPY work_hunter ./work_hunter
COPY docs ./docs

RUN find /app -type f \( -name "*.sh" -o -path "*/docker/*" \) -print0 \
    | xargs -0 -r dos2unix \
    && pip install --no-deps .

VOLUME ["/data"]
ENTRYPOINT ["/usr/bin/tini", "--", "work-hunter"]
CMD ["--root", "/data", "hh-auth-status"]
