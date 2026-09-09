# syntax=docker/dockerfile:1.6

# -----------------------------------------------------------------------------
# Backend builder image
# -----------------------------------------------------------------------------

FROM python:3.12-slim-bookworm AS backend-builder

SHELL ["/bin/bash", "-c"]

ARG APT_MIRROR_URL=https://deb.debian.org/debian
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=pypi.org

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

# Build tools and development headers stay in this disposable stage.
# gettext is required to compile Django message catalogs.
RUN set -eux; \
    sed -i \
        -e "s|http://deb.debian.org/debian|${APT_MIRROR_URL}|g" \
        -e "s|https://deb.debian.org/debian|${APT_MIRROR_URL}|g" \
        /etc/apt/sources.list.d/debian.sources; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        build-essential \
        gettext \
        libmagic-dev \
        libpq-dev \
        libxml2-dev \
        libxslt1-dev \
        pkg-config \
        zlib1g-dev; \
    rm -rf /var/lib/apt/lists/* /tmp/* /root/.cache

RUN pip install \
        --index-url "$PIP_INDEX_URL" \
        --trusted-host "$PIP_TRUSTED_HOST" \
        --timeout 120 \
        --retries 5 \
        uv \
    && python -m venv --without-pip /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV=/opt/venv

WORKDIR /opt/backend

COPY backend /opt/backend
COPY plugins /opt/plugins
COPY pyproject.toml /opt/backend/

ARG DEV_MODE=0
RUN set -eux; \
    compile_options=(); \
    if [ "$DEV_MODE" = "1" ]; then \
        compile_options+=(--extra dev); \
    fi; \
    uv pip compile \
        pyproject.toml \
        -o requirements.txt \
        --index-url "$PIP_INDEX_URL" \
        --trusted-host "$PIP_TRUSTED_HOST" \
        "${compile_options[@]}"; \
    uv pip install \
        --python /opt/venv/bin/python \
        -r requirements.txt \
        --index-url "$PIP_INDEX_URL" \
        --trusted-host "$PIP_TRUSTED_HOST"

# DJANGO_DEBUG is scoped to this build step because production-only settings
# require secrets that are unavailable while the image is built.
RUN DJANGO_DEBUG=true python manage.py compilemessages -l zh_Hans -l en -l es \
    && rm -rf /root/.cache /tmp/* \
    && find /opt/venv -type d -name __pycache__ -prune \
        -exec rm -rf {} + \
    && find /opt/backend -type d -name __pycache__ -prune \
        -exec rm -rf {} +

# -----------------------------------------------------------------------------
# Backend runtime image
# -----------------------------------------------------------------------------

FROM python:3.12-slim-bookworm AS backend

SHELL ["/bin/bash", "-c"]

ARG APT_MIRROR_URL=https://deb.debian.org/debian
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=pypi.org

ENV DEBIAN_FRONTEND=noninteractive \
    PATH="/opt/venv/bin:$PATH" \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_NO_CACHE_DIR=1 \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv

# Keep only runtime tools and shared libraries. Git is used by datasource
# synchronization, curl by container health checks, and psql by operations.
RUN set -eux; \
    sed -i \
        -e "s|http://deb.debian.org/debian|${APT_MIRROR_URL}|g" \
        -e "s|https://deb.debian.org/debian|${APT_MIRROR_URL}|g" \
        /etc/apt/sources.list.d/debian.sources; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        fonts-noto-cjk \
        git \
        libharfbuzz-subset0 \
        libjpeg62-turbo \
        libmagic1 \
        libopenjp2-7 \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        postgresql-client; \
    rm -rf /var/lib/apt/lists/* /tmp/* /root/.cache

# Runtime images must not carry package-install tooling (issue #55). The
# base image bundles pip via ensurepip; the venv copied in below is built
# --without-pip (see the builder stage above), so this is the only
# remaining source of pip in the final image.
RUN rm -rf \
        /usr/local/lib/python3.12/site-packages/pip \
        /usr/local/lib/python3.12/site-packages/pip-*.dist-info \
        /usr/local/bin/pip \
        /usr/local/bin/pip3 \
        /usr/local/bin/pip3.12

ARG DEV_MODE=0
ENV DEV_MODE=${DEV_MODE}

WORKDIR /opt/backend

COPY --from=backend-builder /opt/venv /opt/venv
COPY --from=backend-builder /opt/backend /opt/backend
COPY --from=backend-builder /opt/plugins /opt/plugins

# Verify neither the system install nor the copied venv resurrected pip.
RUN set -eux; \
    if command -v pip >/dev/null 2>&1; then \
        echo "ERROR: pip is present in the runtime image" >&2; exit 1; \
    fi; \
    if python -m pip --version >/dev/null 2>&1; then \
        echo "ERROR: python -m pip works in the runtime image" >&2; exit 1; \
    fi

RUN mkdir -p \
        /var/cache/sourcelens \
        /var/log/celery \
        /var/log/gunicorn

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Keep the version stamp last so version-only changes reuse expensive layers.
ARG APP_VERSION=0.0.0
LABEL com.oneprolabs.sourcelens.version=$APP_VERSION

ENTRYPOINT ["/entrypoint.sh"]
