# syntax=docker/dockerfile:1.7
# Build context: repository root (needs policies/dist and policies/lua).
#   docker build -f control-plane/Dockerfile -t veyrqun/control-plane .

ARG PYTHON_IMAGE=python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2

FROM ${PYTHON_IMAGE} AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY control-plane/requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/venv && /opt/venv/bin/pip install -r /tmp/requirements.txt

FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH" \
    POLICY_WASM_PATH=/app/policies/dist/policy.wasm LUA_DIR=/app/policies/lua
RUN groupadd --system --gid 10001 veyrqun && useradd --system --uid 10001 --gid veyrqun --no-create-home veyrqun
COPY --from=deps /opt/venv /opt/venv
WORKDIR /app/control-plane
COPY --chown=root:root control-plane/ /app/control-plane/
COPY --chown=root:root policies/dist/ /app/policies/dist/
COPY --chown=root:root policies/lua/ /app/policies/lua/
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" || exit 1
# Migrations run as a separate step with the owner role (see docs/Deployment.md), never by the app user.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
