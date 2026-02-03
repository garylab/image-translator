FROM python:3.12.7-slim

RUN apt-get update  \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        mime-support \
        libmagic1 \
        xvfb \
        xauth \
        dbus \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /workspace
ENV PYTHONPATH="${PYTHONPATH}:."

COPY pyproject.toml .
COPY uv.lock .
RUN uv pip install --system -r pyproject.toml

RUN playwright install chromium --with-deps

COPY src/ src/

CMD ["xvfb-run", "--auto-servernum", "--server-args=-screen 0 1920x1080x24", "uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]