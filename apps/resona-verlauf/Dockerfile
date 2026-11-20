# Resona Verlauf — Nuxt 4 SPA (layer app extending ../resona-pwa) served by Nitro.
# Build context is `apps/` (not the app dir) so the layer sibling is present:
#   docker build -f resona-verlauf/Dockerfile apps
# Production MUST run the Nitro server (`nuxt build` → .output/server): the app
# has server routes (server/api/termine.get.ts, the /directus proxy).
#
# `ssr: false`: runtimeConfig.public is baked into the client bundle at BUILD
# time, so the public URLs come in as build args. Server-only secrets (Google
# OAuth, session password) are read at RUNTIME from env.

FROM oven/bun:1.3.14 AS build
WORKDIR /app

# Install deps of the layer and of the app first for layer caching.
COPY resona-pwa/package.json resona-pwa/bun.lock ./resona-pwa/
RUN cd resona-pwa && bun install --frozen-lockfile
COPY resona-verlauf/package.json resona-verlauf/bun.lock ./resona-verlauf/
RUN cd resona-verlauf && bun install --frozen-lockfile

COPY resona-pwa ./resona-pwa
COPY resona-verlauf ./resona-verlauf

ARG NUXT_PUBLIC_DIRECTUS_URL=http://localhost:7700
ARG NUXT_PUBLIC_SSO_DIRECTUS_URL=
ARG NUXT_PUBLIC_RESONA_API_URL=http://localhost:7710
ARG NUXT_PUBLIC_DEFAULT_LANGUAGE=de
ARG NUXT_PUBLIC_DEFAULT_PROFILE=default
ARG NUXT_PUBLIC_CALENDAR_ENABLED=false
ARG NUXT_PUBLIC_DATA_MODE=directus
ENV NUXT_PUBLIC_DIRECTUS_URL=${NUXT_PUBLIC_DIRECTUS_URL} \
	NUXT_PUBLIC_SSO_DIRECTUS_URL=${NUXT_PUBLIC_SSO_DIRECTUS_URL} \
	NUXT_PUBLIC_RESONA_API_URL=${NUXT_PUBLIC_RESONA_API_URL} \
	NUXT_PUBLIC_DEFAULT_LANGUAGE=${NUXT_PUBLIC_DEFAULT_LANGUAGE} \
	NUXT_PUBLIC_DEFAULT_PROFILE=${NUXT_PUBLIC_DEFAULT_PROFILE} \
	NUXT_PUBLIC_CALENDAR_ENABLED=${NUXT_PUBLIC_CALENDAR_ENABLED} \
	NUXT_PUBLIC_DATA_MODE=${NUXT_PUBLIC_DATA_MODE}

WORKDIR /app/resona-verlauf
RUN bun run build

FROM oven/bun:1.3.14-slim AS runtime
WORKDIR /app
# curl: Coolify probes with curl/wget when the image's HEALTHCHECK is ignored.
RUN apt-get update \
	&& apt-get install -y --no-install-recommends curl \
	&& rm -rf /var/lib/apt/lists/*
ENV NODE_ENV=production \
	HOST=0.0.0.0 \
	PORT=3000
COPY --from=build /app/resona-verlauf/.output ./.output
EXPOSE 3000
# Healthcheck via bun's own fetch (no curl/wget needed); Coolify reuses it.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
	CMD bun -e "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
# Server-only secrets (NUXT_GOOGLE_*, NUXT_SESSION_PASSWORD) come from compose at run time.
CMD ["bun", "run", ".output/server/index.mjs"]
