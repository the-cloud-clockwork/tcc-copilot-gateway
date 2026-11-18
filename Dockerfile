FROM python:3.12.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LITELLM_MODE=PRODUCTION

WORKDIR /app
COPY requirements.txt litellm_patches.py ./
RUN pip install -r requirements.txt && python litellm_patches.py
COPY ca_bundle.py copilot_auth.py entrypoint.sh ./

EXPOSE 4000
ENTRYPOINT ["/app/entrypoint.sh"]
