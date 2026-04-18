#!/usr/bin/env bash
set -euo pipefail

# One-shot project setup. Safe to re-run.
# Clones the RAGFlow reference repo (read-only inspiration, gitignored) and
# copies the env template into place. Never overwrites an existing .env.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${ROOT_DIR}"

echo "==> Cloning RAGFlow reference (if missing)..."
if [ -d "reference/ragflow/.git" ]; then
    echo "    reference/ragflow already present, skipping."
else
    git clone --depth 1 https://github.com/infiniflow/ragflow.git reference/ragflow
fi

echo "==> Preparing .env..."
if [ -f ".env" ]; then
    echo "    .env already exists, leaving it alone."
else
    cp .env.example .env
    echo "    Created .env from .env.example. Edit it to set real values."
fi

cat <<'EOF'

Next steps:
  1. Edit .env (secrets, DB creds, LLM provider).
  2. Start the stack:
       docker compose -f docker/docker-compose.yml up --build
  3. Smoke checks:
       curl http://localhost:8000/health
       open http://localhost:3000
EOF
