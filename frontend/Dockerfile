FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# Non-root nginx on 8080; upstreams are filled in from env at start (envsubst templates).
FROM nginxinc/nginx-unprivileged:1.29-alpine
ENV CONTROL_PLANE_UPSTREAM=control-plane:8000 GATEWAY_UPSTREAM=gateway:8080
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
