FROM node:24.22.3-trixie-slim
WORKDIR /app
COPY integrations/telegram/package.json integrations/telegram/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force
COPY integrations/telegram/policy.mjs integrations/telegram/matrix.mjs integrations/telegram/acp.mjs integrations/telegram/control.mjs integrations/telegram/service.mjs integrations/telegram/delegation.mjs ./
COPY agents/connector/telegram-intent.mjs agents/connector/telegram-office.mjs /agents/connector/
USER 991:991
ENTRYPOINT ["node", "/app/service.mjs"]
