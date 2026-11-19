# syntax=docker/dockerfile:1
#
# VYRON web image. Build context is the REPOSITORY ROOT:
#   docker build -f apps/web/Dockerfile -t vyron-web .
#
# STATUS: UNVERIFIED TEMPLATE. It cannot be built yet: the Next.js application (package.json, lockfile,
# next.config, app/ routes) does not exist, and the Docker daemon was not available. It records the
# intended shape: a multi-stage build, Next.js "standalone" output, non-root runtime, no secrets in the
# image. tests/test_docker_config.py lists exactly which COPY sources are still missing.
#
# Pin the base image by digest when you can pull it, for example
#   --build-arg NODE_IMAGE=node:22-alpine@sha256:<digest>
# No digest is recorded here because none could be verified.

ARG NODE_IMAGE=node:22-alpine

# ---------------------------------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS deps
WORKDIR /repo
COPY package.json package-lock.json ./
COPY apps/web/package.json apps/web/package.json
COPY packages/types/package.json packages/types/package.json
COPY packages/api-client/package.json packages/api-client/package.json
COPY packages/graph/package.json packages/graph/package.json
COPY packages/ui/package.json packages/ui/package.json
RUN npm ci --ignore-scripts

# ---------------------------------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS build
WORKDIR /repo
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /repo/node_modules ./node_modules
COPY package.json package-lock.json tsconfig.check.json ./
COPY apps/web ./apps/web
COPY packages ./packages
# Only public values may be baked into a browser bundle. Secrets are read by the server at run time.
ARG NEXT_PUBLIC_APP_URL=http://localhost:3000
ENV NEXT_PUBLIC_APP_URL=${NEXT_PUBLIC_APP_URL}
RUN npm run build --workspace apps/web

# ---------------------------------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS runtime
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
WORKDIR /app
RUN addgroup -S -g 10001 vyron && adduser -S -u 10001 -G vyron -H -h /nonexistent -s /sbin/nologin vyron
COPY --from=build --chown=10001:10001 /repo/apps/web/.next/standalone ./
COPY --from=build --chown=10001:10001 /repo/apps/web/.next/static ./apps/web/.next/static
COPY --from=build --chown=10001:10001 /repo/apps/web/public ./apps/web/public
USER 10001:10001
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD ["node", "-e", "fetch('http://127.0.0.1:3000/api/health').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"]
CMD ["node", "apps/web/server.js"]
