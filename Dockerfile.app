# Always-on app image: dashboard + Telegram alert scheduler (docker-compose.yml).
# The minimal CLI image stays in ./Dockerfile.

FROM node:22-alpine AS ui
WORKDIR /ui
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY dashboard/index.html dashboard/vite.config.js ./
COPY dashboard/src ./src
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Kolkata
WORKDIR /app
COPY india ./india
COPY workflow ./workflow
COPY research ./research
COPY legacy ./legacy
COPY config ./config
COPY dashboard/*.py ./dashboard/
COPY --from=ui /ui/dist ./dashboard/dist
# var/ (state, Telegram secrets) and trader/ (workspace) are mounted at runtime.
RUN mkdir -p var trader && chown 10001:10001 var trader
USER 10001:10001
EXPOSE 8765
CMD ["python", "-m", "india.picks", "--config", "config/picks.json", "--serve"]
