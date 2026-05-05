# syntax=docker/dockerfile:1.7
# =============================================================================
# EasyObs Web Console (Next.js 15 standalone) — production multi-stage image.
#
# Build context (반드시): docs/comparison/03.develop/easyobs/apps/web
#
#   docker build \
#     -f docs/comparison/03.develop/easyobs/setup/images/web/Dockerfile \
#     -t easyobs/web:0.2.0 \
#     --build-arg NEXT_PUBLIC_API_URL=http://api:8787 \
#     docs/comparison/03.develop/easyobs/apps/web
# =============================================================================

# ---- Stage 1: deps ----------------------------------------------------------
FROM node:20-alpine AS deps

WORKDIR /app

COPY package.json package-lock.json* ./

RUN if [ -f package-lock.json ]; then \
      npm ci --no-audit --no-fund ; \
    else \
      npm install --no-audit --no-fund ; \
    fi

# ---- Stage 2: builder -------------------------------------------------------
FROM node:20-alpine AS builder

WORKDIR /app

# next.config.ts 는 `output: "standalone"` 으로 설정되어 있어
# .next/standalone + .next/static 두 산출물만 가지고 가면 됨.
ARG NEXT_PUBLIC_API_URL=http://127.0.0.1:8787
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# public/ 은 선택 디렉터리(현재 소스에는 없음). 비어 있어도 standalone 산출물에서
# 안전하게 복사할 수 있도록 미리 만들어 둔다.
RUN mkdir -p public

RUN npm run build

# ---- Stage 3: runtime -------------------------------------------------------
FROM node:20-alpine AS runtime

WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1100 nodeapp \
 && adduser  --system --uid 1100 --ingroup nodeapp nodeapp \
 && apk add --no-cache curl

# Next.js standalone output 만 복사 (node_modules 통째 X)
COPY --from=builder --chown=nodeapp:nodeapp /app/.next/standalone ./
COPY --from=builder --chown=nodeapp:nodeapp /app/.next/static ./.next/static
COPY --from=builder --chown=nodeapp:nodeapp /app/public ./public

USER nodeapp

EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=10 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/" >/dev/null || exit 1

CMD ["node", "server.js"]
