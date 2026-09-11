# ---------- Stage 1: Frontend build ----------
FROM node:20-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0 AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- Stage 2: Backend runtime (arm64 + amd64 compatible) ----------
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIWATCH_STATIC_DIR=/app/static
# nvme-cli: only used by the node-agent's NVMe SMART reader (node_agent.py), and only
# works there when the DaemonSet grants it privileged+/dev access (see
# deploy/daemonset-node-agent.yaml) -- harmless/unused in the backend Deployment.
RUN apt-get update && apt-get install -y --no-install-recommends nvme-cli \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt .
# Hash-locked (pip-compile --generate-hashes): every wheel is verified against the lock.
RUN pip install --no-cache-dir --require-hashes -r requirements.txt
COPY backend/app ./app
COPY --from=frontend /build/dist ./static
# Non-root runtime user for the backend (numeric so runAsNonRoot can be
# verified); nothing inside the image needs to be writable - the history db
# lives on a volume. The node-agent DaemonSet overrides this back to root
# explicitly: privileged capabilities are only effective for UID 0, and the
# NVMe SMART ioctl needs them (see deploy/daemonset-node-agent.yaml).
RUN useradd --uid 10001 --user-group --no-create-home piwatch
ENV HOME=/tmp
USER 10001
EXPOSE 8000
# Backend (default). The node-agent uses the same image with a different command
# (see deploy/daemonset-node-agent.yaml).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
