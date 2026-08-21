FROM node:22-alpine

WORKDIR /app

COPY package.json ./
RUN npm install

COPY . .

EXPOSE 5173

# Listens on 0.0.0.0 inside the container; host publication is loopback-only.
CMD ["npm", "run", "dev"]
