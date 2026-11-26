FROM node:24.22.3-bookworm-slim
WORKDIR /app
COPY --chmod=0644 package.json package-lock.json ./
RUN npm ci --omit=dev
COPY --chmod=0644 monitor.mjs pins.mjs telegram-space.mjs ./
ENV NODE_ENV=production
USER 991:991
CMD ["node", "monitor.mjs"]
