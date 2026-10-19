# syntax=docker/dockerfile:1
# 前端两阶段：先构建出静态资源，再用 nginx 托管。
# 构建环境（有 node_modules、有 devDependencies）不会进最终镜像。
FROM node:22-alpine AS build

WORKDIR /app

COPY package*.json ./
RUN npm ci --registry=https://registry.npmmirror.com

COPY . .
RUN npm run build

FROM nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
