FROM node:22-alpine

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --omit=dev

COPY src/ ./src/
COPY data/ ./data/

EXPOSE 3030
HEALTHCHECK --interval=30s --timeout=5s CMD node -e "require('http').get('http://localhost:3030/health',r=>{process.exit(r.statusCode===200?0:1)})"

CMD ["node", "src/sse.mjs"]
