# Camilia — AI Agent Factory dashboard
# Stage 1 builds the static bundle; stage 2 serves it and proxies the API.

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund && npm rebuild esbuild
COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN NODE_OPTIONS=--max-old-space-size=768 npm run build

FROM nginx:1.27-alpine AS runtime
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
RUN addgroup -S camilia 2>/dev/null || true \
 && chown -R nginx:nginx /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD wget -qO- http://127.0.0.1/healthz-static >/dev/null || exit 1
