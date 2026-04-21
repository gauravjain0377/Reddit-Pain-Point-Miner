# ── Stage 1: Builder ─────────────────────────────────────────────────────────
# Use slim for a smaller final image. 3.11 matches the dev environment.
FROM python:3.11-slim AS builder

WORKDIR /app

# Install dependencies first (separate layer → cached unless requirements changes)
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim

# Create a non-root user for security
RUN useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy only the backend source (not frontend, .env, tests, or caches)
COPY --chown=appuser:appuser . .

# Switch to non-root user
USER appuser

# Expose the port Uvicorn will listen on
EXPOSE 8000

# Health check — Railway and Render use this to determine when the container
# is ready to receive traffic
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"

# Run with Uvicorn. --workers 1 is appropriate for the free tier memory limits.
# Increase to 2-4 on paid plans with multiple vCPUs.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--log-level", "info"]
