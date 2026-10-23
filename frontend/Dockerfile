# Билдим SPA через node, отдаём через nginx.
# Nginx также проксирует /api → webapp:8000 и WS-канал /api/events.
FROM node:20-alpine AS build
WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY . .
RUN npm run build


FROM nginx:1.27-alpine
RUN apk add --no-cache openssl
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY nginx-locations.conf /etc/nginx/affectra-locations.conf
COPY --chmod=755 10-affectra-cert.sh /docker-entrypoint.d/10-affectra-cert.sh

EXPOSE 80 443
CMD ["nginx", "-g", "daemon off;"]
