# Chris Li 个人网站 —— 基于 nginx 的静态站点镜像
FROM nginx:1.27-alpine

# 使用自定义 nginx 站点配置
RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/site.conf

# 拷贝静态站点文件
COPY site/ /usr/share/nginx/html/

EXPOSE 80

# 容器健康检查
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
