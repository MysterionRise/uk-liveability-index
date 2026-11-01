# Front end (Next.js standalone output). NEXT_PUBLIC_* values are baked in at build time:
# they are what the *browser* uses to reach the API and the data files. The defaults (an
# empty API URL and /data) mean the page's own origin, through the proxy (docker/Caddyfile),
# so one image works on any host and port.
FROM node:24-alpine AS build
WORKDIR /app
ARG NEXT_PUBLIC_API_URL=
ARG NEXT_PUBLIC_DATA_URL=/data
# 1 exposes the map as window.__map for browser tests against this image
ARG NEXT_PUBLIC_EXPOSE_MAP=0
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL NEXT_PUBLIC_DATA_URL=$NEXT_PUBLIC_DATA_URL \
    NEXT_PUBLIC_EXPOSE_MAP=$NEXT_PUBLIC_EXPOSE_MAP \
    NEXT_TELEMETRY_DISABLED=1 COPILOTKIT_TELEMETRY_DISABLED=true
COPY web/package.json web/package-lock.json web/.npmrc ./
RUN npm ci
COPY web ./
# The methodology and sources pages render these (scripts/copy-docs.mjs reads ../docs)
COPY docs /docs
COPY ATTRIBUTION.md DATA-LICENCE.md /
RUN npm run build

FROM node:24-alpine
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 COPILOTKIT_TELEMETRY_DISABLED=true PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
