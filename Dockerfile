# Mortar, bnb as a web app: the web UI built with Node, then served by gunicorn from a slim Python image.
#   docker build -t mortar .
#   docker run -p 8080:8080 -v bnb-data:/data --env-file mortar.env mortar
FROM node:24-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN mkdir -p /src/mortar/server && npm run build

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MORTAR_DATA=/data
RUN useradd --system --uid 10001 --home-dir /data bnb && mkdir /data && chown bnb /data
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/
COPY --from=web /src/mortar/server/static/ src/mortar/server/static/
RUN pip install --no-cache-dir ".[server,postgres,market]"
USER bnb
VOLUME /data
EXPOSE 8080
# One process keeps the per-user caches in one place; threads serve requests side by side.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "16", "--timeout", "180", \
     "--access-logfile", "-", "mortar.server.app:create_app()"]
