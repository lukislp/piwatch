# ---------- Stage 1: Frontend build ----------
FROM node:20-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- Stage 2: Backend runtime (arm64 + amd64 compatible) ----------
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIWATCH_STATIC_DIR=/app/static
# nvme-cli: only used by the node-agent's NVMe SMART reader (node_agent.py), and only
# works there when the DaemonSet grants it privileged+/dev access (see
# deploy/daemonset-node-agent.yaml) -- harmless/unused in the backend Deployment.
RUN apt-get update && apt-get install -y --no-install-recommends nvme-cli \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=frontend /build/dist ./static
EXPOSE 8000
# Backend (default). The node-agent uses the same image with a different command
# (see deploy/daemonset-node-agent.yaml).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
