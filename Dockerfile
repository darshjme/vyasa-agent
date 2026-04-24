# SPDX-License-Identifier: Apache-2.0
# syntax=docker/dockerfile:1.7

# -----------------------------------------------------------------------------
# Stage 1 — build: resolve + install deps into a self-contained venv with uv.
# -----------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:0.11.6 AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=automatic \
    UV_PYTHON=3.12 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Copy only dependency manifests first for better layer caching.
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY vyasa_agent/__init__.py vyasa_agent/__init__.py

# Prime the virtualenv layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/vyasa-venv && \
    uv pip install --python /opt/vyasa-venv/bin/python --upgrade pip setuptools wheel

# Copy the full source tree and install the project itself.
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /opt/vyasa-venv/bin/python \
        ".[all]"

# -----------------------------------------------------------------------------
# Stage 2 — runtime: minimal slim image, non-root user, no build toolchain.
# -----------------------------------------------------------------------------
FROM python:3.14-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/vyasa-venv/bin:${PATH}" \
    VYASA_HOME=/var/lib/vyasa

RUN apt-get update \
 && apt-get install --no-install-recommends -y \
        ca-certificates tini curl \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 10000 vyasa \
 && useradd  --system --uid 10000 --gid vyasa \
             --home-dir ${VYASA_HOME} --shell /usr/sbin/nologin vyasa \
 && mkdir -p ${VYASA_HOME}/employees ${VYASA_HOME}/logs \
 && chown -R vyasa:vyasa ${VYASA_HOME}

# Bring the resolved venv and the package source.
COPY --from=builder /opt/vyasa-venv /opt/vyasa-venv
COPY --from=builder --chown=vyasa:vyasa /build/vyasa_agent /app/vyasa_agent
COPY --from=builder --chown=vyasa:vyasa /build/employees   /app/employees
COPY --from=builder --chown=vyasa:vyasa /build/plans       /app/plans
COPY --from=builder --chown=vyasa:vyasa /build/plugins     /app/plugins
COPY --from=builder --chown=vyasa:vyasa /build/skills      /app/skills
COPY --from=builder --chown=vyasa:vyasa /build/NOTICE      /app/NOTICE
COPY --from=builder --chown=vyasa:vyasa /build/LICENSE     /app/LICENSE

WORKDIR /app
VOLUME ["/var/lib/vyasa"]

EXPOSE 8644 8645

USER vyasa:vyasa

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:8644/healthz" >/dev/null || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["vyasa", "gateway", "serve"]
