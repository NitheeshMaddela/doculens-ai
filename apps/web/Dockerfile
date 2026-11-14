FROM node:22-bookworm-slim AS build
WORKDIR /web
ENV NEXT_TELEMETRY_DISABLED=1
COPY package*.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ENV API_INTERNAL_URL=http://api:8000
RUN npm run build && npm prune --omit=dev --no-audit --no-fund
FROM node:22-bookworm-slim
WORKDIR /web
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1
COPY --from=build --chown=node:node /web /web
USER node
CMD ["npm", "start", "--", "--hostname", "0.0.0.0"]
