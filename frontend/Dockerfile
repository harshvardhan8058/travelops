FROM node:22-bookworm-slim

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    VERIFY_API_BASE_URL=http://api:8000/api/v1

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci \
    && npx playwright install --with-deps chromium

COPY . .

EXPOSE 5173

# Listens on 0.0.0.0 inside the container; host publication is loopback-only.
CMD ["npm", "run", "dev"]
