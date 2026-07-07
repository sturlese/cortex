# syntax=docker/dockerfile:1
#
# gbrain image — ONE image, used by BOTH gbrain containers (serve + autopilot).
# Only the command differs (set in docker-compose.yml). The Tailscale sidecar
# uses the official tailscale/tailscale image instead (see docker-compose.yml).
#
# Reproducibility:
#   - GBRAIN_REF is a full git commit SHA of garrytan/gbrain (immutable).
#   - BUN_VERSION pins the Bun base image.
#   gbrain is installed from source (Bun runs the TS entrypoint directly), so all
#   on-disk assets (skills/, schema files) stay intact — no fragile compile step.

ARG BUN_VERSION=1.3.10
FROM oven/bun:${BUN_VERSION}

# Pinned gbrain release = v0.42.34.0. Override at build time with:
#   docker build --build-arg GBRAIN_REF=<sha> .
ARG GBRAIN_REF=099d9a8f5505f1d64e4f4f784f8429cc2d47c18f

USER root

# Global bun installs land here; on PATH regardless of the base image's default user.
ENV BUN_INSTALL=/usr/local/bun
ENV PATH=/usr/local/bun/bin:${PATH}

# ca-certificates: TLS to Supabase / Anthropic / ZeroEntropy.
# curl: container healthcheck. git: bun needs it to fetch the pinned GitHub ref.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl git \
 && rm -rf /var/lib/apt/lists/*

RUN bun install -g "github:garrytan/gbrain#${GBRAIN_REF}" \
 && gbrain --version

# Local state (audit logs, supervisor pid). Mounted as a named volume in compose.
# With Supabase, the heavy state lives in Postgres — this volume is light.
ENV GBRAIN_HOME=/data
RUN mkdir -p /data

EXPOSE 3131

ENTRYPOINT ["gbrain"]
CMD ["serve", "--http", "--bind", "0.0.0.0", "--port", "3131"]
