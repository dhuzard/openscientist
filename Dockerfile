# syntax=docker/dockerfile:1

# Dockerfile for OpenScientist
# Builds on openscientist-base which includes Python, Node.js, uv, and Claude CLI

# --- Stage: build the open-codex CLI from the fork --------------------------
# The open-codex branch carries the fixes that let codex drive open-weight
# models served by Ollama: flatten MCP namespace tools for providers without
# namespace support, a function-form apply_patch (with a +-prefix patch-body
# description so open models format added lines correctly), and a web_search
# capability gate that stops the hosted tool from corrupting tool calls on
# non-OpenAI providers. Pinned to a commit on open-codex for reproducibility.
FROM rust:1.95-bookworm AS codex-build
ARG CODEX_REPO=https://github.com/LucaCappelletti94/codex.git
ARG CODEX_REF=8f8009fcab89baafa51c15c9542734b1c94de8b6
RUN set -eux; \
    git init /codex; \
    git -C /codex remote add origin "${CODEX_REPO}"; \
    attempt=1; \
    while true; do \
        if git -c http.version=HTTP/1.1 -C /codex fetch \
            --depth=1 --no-tags origin "${CODEX_REF}"; then \
            break; \
        fi; \
        if [ "${attempt}" -ge 5 ]; then \
            echo "Failed to fetch Codex commit after ${attempt} attempts" >&2; \
            exit 1; \
        fi; \
        delay=$((attempt * 10)); \
        echo "Codex fetch attempt ${attempt} failed; retrying in ${delay}s" >&2; \
        sleep "${delay}"; \
        attempt=$((attempt + 1)); \
    done; \
    git -C /codex checkout --detach FETCH_HEAD
RUN --mount=type=cache,id=codex-cargo-registry,target=/usr/local/cargo/registry,sharing=locked \
    --mount=type=cache,id=codex-cargo-git,target=/usr/local/cargo/git,sharing=locked \
    --mount=type=cache,id=codex-cargo-target,target=/codex/codex-rs/target,sharing=locked \
    cargo build --release --manifest-path /codex/codex-rs/Cargo.toml -p codex-cli \
    && cp /codex/codex-rs/target/release/codex /usr/local/bin/codex

FROM openscientist-base:latest

# Build args
ARG OPENSCIENTIST_COMMIT=unknown
ARG BUILD_TIME=unknown

# Optionally install Phenix for structural biology
# Requires data/phenix-installer-*.tar.gz to be present
RUN if [ "$INSTALL_PHENIX" = "true" ]; then \
        INSTALLER=$(ls /tmp/phenix-installer-*.tar.gz 2>/dev/null | head -1) && \
        if [ -n "$INSTALLER" ]; then \
            cd /tmp && tar xzf "$INSTALLER" && \
            cd phenix-installer-* && ./install --prefix=/opt && \
            cd / && rm -rf /tmp/phenix-installer-*; \
        fi; \
    fi

# Set working directory
WORKDIR /app

# WeasyPrint system libraries (libpango/cairo/gdk-pixbuf/glib). Needed so any
# web-side PDF rendering matches the agent's WeasyPrint output rather than
# falling back to fpdf2. Mirrors the agent image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libcairo2 \
    libffi8 \
    shared-mime-info \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Install the open-codex CLI built in the codex-build stage above. Used by
# discovery AND in-page job chat when an OpenAI/codex/Ollama provider is
# selected (the chat agent runs in this web process, so it needs codex here).
# Built from the fork rather than the upstream release because the open-weight
# fixes are not yet upstream.
COPY --from=codex-build /usr/local/bin/codex /usr/local/bin/codex
RUN chmod +x /usr/local/bin/codex

# Copy project files — deps already installed in base
COPY pyproject.toml README.md alembic.ini uv.lock ./
COPY requirements/udwa-poc.txt ./requirements/udwa-poc.txt
COPY src/ src/

# Reinstall the locked dependency graph and project so the web runtime cannot
# silently drift from the versions exercised by CI and the agent image.
RUN uv export --locked --no-dev --no-emit-project --no-hashes \
        --output-file /tmp/openscientist-requirements.txt \
    && uv pip install --system -r /tmp/openscientist-requirements.txt \
    && uv pip install --system --no-deps -e . \
    && rm -f /tmp/openscientist-requirements.txt

# The trusted DVC gateway runs in this web process, so it needs the same pinned
# UDWA acquisition boundary as the agent image. Non-DVC builds may leave
# INSTALL_UDWA=false; the FAIR/DVC deployment overlay sets it to true and
# supplies the private-repository credential as a transient BuildKit secret.
ARG INSTALL_UDWA=false
RUN --mount=type=secret,id=github_token \
    set -eu; \
    if [ "${INSTALL_UDWA}" != "true" ]; then \
        echo "Skipping optional UDWA install"; \
        exit 0; \
    fi; \
    if [ ! -s /run/secrets/github_token ]; then \
        echo "INSTALL_UDWA=true requires the github_token BuildKit secret" >&2; \
        exit 1; \
    fi; \
    askpass="$(mktemp)"; \
    trap 'rm -f "${askpass}"' EXIT; \
    printf '%s\n' \
        '#!/bin/sh' \
        'case "$1" in' \
        '  *Username*) printf "%s\n" "x-access-token" ;;' \
        '  *Password*) cat /run/secrets/github_token ;;' \
        '  *) exit 1 ;;' \
        'esac' \
        > "${askpass}"; \
    chmod 700 "${askpass}"; \
    GIT_ASKPASS="${askpass}" GIT_TERMINAL_PROMPT=0 \
        uv pip install --system -r requirements/udwa-poc.txt

# Create jobs directory
RUN mkdir -p jobs

# Expose port for NiceGUI
EXPOSE 8080

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV OPENSCIENTIST_COMMIT=${OPENSCIENTIST_COMMIT}
ENV OPENSCIENTIST_BUILD_TIME=${BUILD_TIME}
# Fixed path for GCP credentials (mounted via GCP_CREDENTIALS_FILE in docker-compose)
ENV GOOGLE_APPLICATION_CREDENTIALS=/app/gcp-credentials.json

CMD ["python", "-m", "openscientist.web_app", "--host", "0.0.0.0", "--port", "8080"]
