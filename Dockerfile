# Mason as a web app: the web UI built with Node, then served by gunicorn from a slim Python image.
#   docker build -t mason .
#   docker run -p 8080:8080 -v mason-data:/data --env-file mason.env mason
FROM node:24-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN mkdir -p /src/mason/server && npm run build

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MASON_DATA=/data
RUN useradd --system --uid 10001 --home-dir /data mason && mkdir /data && chown mason /data
WORKDIR /app
COPY pyproject.toml hatch_build.py README.md LICENSE ./
COPY src/ src/
COPY --from=web /src/mason/server/static/ src/mason/server/static/
RUN pip install --no-cache-dir ".[server,postgres,market]"
USER mason
VOLUME /data
EXPOSE 8080
# One process keeps the per-user caches in one place; threads serve requests side by side.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "16", "--timeout", "180", \
     "--access-logfile", "-", "mason.server.app:create_app()"]
