FROM node:24.22.3-trixie-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates poppler-utils unzip && rm -rf /var/lib/apt/lists/*
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY *.mjs ./
RUN mkdir -p /agents && ln -s /app /agents/connector
USER 991:991
CMD ["node", "/app/connector.mjs"]
