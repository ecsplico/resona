# Resona PWA — Nuxt 4 SPA served by its Nitro Node server.
# Because the app has server routes (the sanctioned Google Calendar OAuth layer,
# server/api/calendar/**), production MUST run the Nitro server (`nuxt build` →
# run .output/server) — NOT `nuxt generate` static output (which strips it).
#
# The app is `ssr: false`, so runtimeConfig.public is baked into the client
# bundle at BUILD time. Those URLs are consumed by the BROWSER, so they must be
# host-reachable (localhost:<published port>), not compose service names — pass
# them as build args. Server-only secrets (Google OAuth, session password) are
# read at RUNTIME from env, so they stay out of the image.

FROM oven/bun:1.3.14 AS build
WORKDIR /app

# Install deps first for layer caching.
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile

COPY . .

# Public config — baked into the client bundle. Defaults match the published
# host ports in docker-compose.full.yml.
ARG NUXT_PUBLIC_DIRECTUS_URL=http://localhost:7700
ARG NUXT_PUBLIC_RESONA_API_URL=http://localhost:7710
ARG NUXT_PUBLIC_DEFAULT_LANGUAGE=de
ARG NUXT_PUBLIC_DEFAULT_PROFILE=default
ARG NUXT_PUBLIC_CALENDAR_ENABLED=false
ARG NUXT_PUBLIC_DATA_MODE=directus
ENV NUXT_PUBLIC_DIRECTUS_URL=${NUXT_PUBLIC_DIRECTUS_URL} \
	NUXT_PUBLIC_RESONA_API_URL=${NUXT_PUBLIC_RESONA_API_URL} \
	NUXT_PUBLIC_DEFAULT_LANGUAGE=${NUXT_PUBLIC_DEFAULT_LANGUAGE} \
	NUXT_PUBLIC_DEFAULT_PROFILE=${NUXT_PUBLIC_DEFAULT_PROFILE} \
	NUXT_PUBLIC_CALENDAR_ENABLED=${NUXT_PUBLIC_CALENDAR_ENABLED} \
	NUXT_PUBLIC_DATA_MODE=${NUXT_PUBLIC_DATA_MODE}

RUN bun run build

FROM oven/bun:1.3.14-slim AS runtime
WORKDIR /app
# curl is required by orchestrators that probe the container with their own
# healthcheck command rather than honouring the image's HEALTHCHECK (Coolify
# shells out to curl/wget and rolls the deploy back when both are missing).
RUN apt-get update \
	&& apt-get install -y --no-install-recommends curl \
	&& rm -rf /var/lib/apt/lists/*
ENV NODE_ENV=production \
	HOST=0.0.0.0 \
	PORT=3000
COPY --from=build /app/.output ./.output
EXPOSE 3000
# Healthcheck via bun's own fetch: this image ships neither curl nor wget, and
# Coolify's default Dockerfile healthcheck shells out to one of them (container
# then reports unhealthy and the deploy rolls back). Declaring HEALTHCHECK here
# makes Coolify reuse it instead of probing with curl/wget.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
	CMD bun -e "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
# Server-only secrets (NUXT_GOOGLE_*, NUXT_SESSION_PASSWORD) are supplied at run
# time by compose and read server-side via useRuntimeConfig(event).
CMD ["bun", "run", ".output/server/index.mjs"]
