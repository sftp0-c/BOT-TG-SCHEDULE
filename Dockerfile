FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

# Сертификаты НУЦ Минцифры для стабильного HTTPS-доступа к российским сайтам (collegelan.ru)
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

# Ссылка Europe/Yekaterinburg -> Asia/Yekaterinburg на случай явного TZ
RUN mkdir -p /usr/share/zoneinfo/Europe \
    && ln -snf ../Asia/Yekaterinburg /usr/share/zoneinfo/Europe/Yekaterinburg

WORKDIR /app

COPY requirements.txt .
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
RUN pip install --prefer-binary -i "$PIP_INDEX_URL" -r requirements.txt

COPY config.py clock.py commands.py state.py schedule_source.py schedule_render.py bot.py ./
COPY samples ./samples
COPY tests ./tests

# Проверка импортов на этапе сборки
RUN python -c "import config, clock, commands, state, schedule_source, schedule_render, bot; print('импорты в порядке')"

RUN mkdir -p /app/data \
    && chmod -R a+rX /app \
    && useradd --system --uid 10001 --home-dir /app app \
    && chown -R app:app /app/data

USER app

STOPSIGNAL SIGINT

CMD ["python", "bot.py"]
