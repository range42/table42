FROM python:3.12-slim-bookworm@sha256:2ed6491b93cd49272ee6de2b5a38440c3448360322c089fc23e370722d74179d
LABEL org.opencontainers.image.source="https://github.com/range42/table42" \
      org.opencontainers.image.licenses="GPL-3.0-only"
WORKDIR /app
RUN python -m pip install --no-cache-dir uv==0.9.30 \
    && useradd --uid 10001 --create-home table42 \
    && install -d -o 10001 -g 10001 /data
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --python 3.12
COPY app ./app
COPY LICENSE /usr/share/doc/table42/LICENSE
USER 10001:10001
ENV RANGE42_DATA_DIR=/data RANGE42_SECURE_COOKIE=true PYTHONDONTWRITEBYTECODE=1
EXPOSE 8042
CMD ["/app/.venv/bin/uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8042"]
