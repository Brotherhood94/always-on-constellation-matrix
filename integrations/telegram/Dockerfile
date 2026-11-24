FROM node:24.22.3-trixie-slim
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force
COPY policy.mjs matrix.mjs acp.mjs control.mjs service.mjs ./
USER 991:991
ENTRYPOINT ["node", "/app/service.mjs"]
