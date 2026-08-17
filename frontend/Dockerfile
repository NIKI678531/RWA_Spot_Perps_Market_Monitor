# Build context is the repository root (see compose.yaml), so paths are prefixed.

FROM node:22-alpine AS build

WORKDIR /app

# Dependencies first: they change far less often than src/, so this layer is reused
# across every application-only rebuild.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./

# Where the app is mounted. The bundle's asset URLs and the router basename are both
# baked in at build time, so this cannot be changed at runtime.
ARG FRONTEND_BASE_PATH=/
ENV FRONTEND_BASE_PATH=${FRONTEND_BASE_PATH}
RUN npm run build

FROM nginx:1.27-alpine

COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
