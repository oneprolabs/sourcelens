# syntax=docker/dockerfile:1.6

# -----------------------------------------------------------------------------
# LensNode builder image
# -----------------------------------------------------------------------------

FROM python:3.12-slim-bookworm AS builder

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=pypi.org

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /opt/lensnode

RUN pip install \
        --index-url "$PIP_INDEX_URL" \
        --trusted-host "$PIP_TRUSTED_HOST" \
        --timeout 120 \
        --retries 5 \
        uv \
    && python -m venv --without-pip /opt/venv

COPY lensnode/pyproject.toml ./
COPY lensnode/lensnode ./lensnode

RUN uv pip install \
        --python /opt/venv/bin/python \
        --index-url "$PIP_INDEX_URL" \
        --trusted-host "$PIP_TRUSTED_HOST" \
        . \
    && rm -rf /root/.cache /tmp/* \
    && find /opt/venv -type d -name __pycache__ -prune \
        -exec rm -rf {} +

# -----------------------------------------------------------------------------
# LensNode runtime image
# -----------------------------------------------------------------------------

FROM python:3.12-slim-bookworm AS runtime

ARG APT_MIRROR_URL=https://deb.debian.org/debian
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=pypi.org
ARG INSTALL_CODEGRAPH=true
ARG CODEGRAPH_VERSION=1.5.0
ARG CODEGRAPH_REGISTRY=https://registry.npmjs.org

ENV PATH="/opt/venv/bin:$PATH" \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_NO_CACHE_DIR=1 \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv

WORKDIR /opt/lensnode

RUN set -eux; \
    sed -i \
        -e "s|http://deb.debian.org/debian|${APT_MIRROR_URL}|g" \
        -e "s|https://deb.debian.org/debian|${APT_MIRROR_URL}|g" \
        /etc/apt/sources.list.d/debian.sources; \
    apt-get update; \
    apt-get install -y --no-install-recommends git ripgrep curl; \
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

# CodeGraph self-contained bundle (no Node/npm in the runtime image).
# Uses the npm registry tarball and links the launcher onto PATH.
ARG TARGETARCH
RUN set -eux; \
    if [ "$INSTALL_CODEGRAPH" = "true" ]; then \
        case "${TARGETARCH:-amd64}" in \
            arm64) cg_arch="arm64" ;; \
            *) cg_arch="x64" ;; \
        esac; \
        mkdir -p /opt/codegraph; \
        curl -fsSL \
            "${CODEGRAPH_REGISTRY}/@colbymchenry/codegraph-linux-${cg_arch}/-/codegraph-linux-${cg_arch}-${CODEGRAPH_VERSION}.tgz" \
            -o /tmp/codegraph.tgz; \
        tar -xzf /tmp/codegraph.tgz -C /opt/codegraph --strip-components=1; \
        ln -s /opt/codegraph/bin/codegraph /usr/local/bin/codegraph; \
        rm -f /tmp/codegraph.tgz; \
    fi

COPY --from=builder /opt/venv /opt/venv
COPY lensnode/docker/entrypoint.sh /entrypoint.sh
COPY plugins /opt/plugins

RUN chmod +x /entrypoint.sh

# Verify neither the system install nor the copied venv resurrected pip.
RUN set -eux; \
    if command -v pip >/dev/null 2>&1; then \
        echo "ERROR: pip is present in the runtime image" >&2; exit 1; \
    fi; \
    if python -m pip --version >/dev/null 2>&1; then \
        echo "ERROR: python -m pip works in the runtime image" >&2; exit 1; \
    fi; \
    if [ "$INSTALL_CODEGRAPH" = "true" ]; then \
        test -x /usr/local/bin/codegraph; \
    fi

ENTRYPOINT ["/entrypoint.sh"]
CMD ["lensnode"]
