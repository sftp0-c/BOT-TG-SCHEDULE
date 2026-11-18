# syntax=docker/dockerfile:1.7

ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE} AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

ARG INSTALL_RU_CA=1
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl; \
    if [ "$INSTALL_RU_CA" = "1" ]; then \
        for name in root sub; do \
            file="/usr/local/share/ca-certificates/russian_trusted_${name}_ca.crt"; \
            curl -fsSL "https://gu-st.ru/content/lending/russian_trusted_${name}_ca_pem.crt" -o "$file"; \
            grep -q "BEGIN CERTIFICATE" "$file"; \
        done; \
    fi; \
    update-ca-certificates; \
    apt-get purge -y --auto-remove curl; \
    rm -rf /var/lib/apt/lists/*

RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin app \
    && install -d -o app -g app -m 0700 /app/data

WORKDIR /app
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/


FROM base AS test-env

COPY requirements.txt requirements-test.txt ./
RUN pip install --prefer-binary -i "$PIP_INDEX_URL" -r requirements-test.txt \
    && python -m pip check

COPY --chown=app:app config.py clock.py commands.py state.py schedule_source.py schedule_render.py bot.py pyproject.toml ./
COPY --chown=app:app samples ./samples
COPY --chown=app:app tests ./tests

USER app
ENV HOME=/tmp \
    TMPDIR=/tmp \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1


FROM test-env AS test
RUN --network=none python -m pytest -q -W error::FutureWarning -o cache_dir=/tmp/pytest_cache


FROM base AS runtime

COPY requirements.txt ./
RUN pip install --prefer-binary -i "$PIP_INDEX_URL" -r requirements.txt \
    && python -m pip check

COPY --chown=app:app config.py clock.py commands.py state.py schedule_source.py schedule_render.py bot.py ./
RUN python -c "import config, clock, commands, state, schedule_source, schedule_render, bot; print('импорты в порядке')"

USER app
STOPSIGNAL SIGINT
CMD ["python", "bot.py"]
