# Phase 6 – Step 6.2: container image for the TRACE Streamlit review app (deployed to Cloud Run)
#
# Secrets are NOT in the image: OPENAI_API_KEY is injected at runtime (Secret Manager on Cloud Run,
# --env-file locally), and .dockerignore keeps .env out of the build context entirely.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8080

WORKDIR /app

# Phase 6 – Step 6.2: run as an unprivileged user, never as root
RUN useradd --create-home --uid 10001 appuser

# Dependencies first: this slow layer is reused from cache when only the code changes
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=appuser:appuser src/ src/
COPY --chown=appuser:appuser app/ app/
COPY --chown=appuser:appuser project_data/ project_data/
COPY --chown=appuser:appuser .streamlit/ .streamlit/

# Audit log and SAR folders must be writable; on Cloud Run they are temporary (Stage 06 decision)
RUN mkdir -p logs outputs && chown -R appuser:appuser /app

USER appuser
EXPOSE 8080

# Cloud Run sets $PORT; 0.0.0.0 makes the server reachable from outside the container
CMD ["sh", "-c", "streamlit run app/streamlit_app.py --server.port=${PORT} --server.address=0.0.0.0"]
