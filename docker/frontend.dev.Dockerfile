# Development frontend: source is bind-mounted, Vite serves and proxies to the API.
FROM node:24-alpine

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
