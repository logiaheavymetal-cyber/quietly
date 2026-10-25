# Quietly + SearXNG en un solo contenedor (pensado para Render, plan gratuito)
FROM searxng/searxng:latest
USER root
RUN (apk add --no-cache nodejs) || (apt-get update && apt-get install -y --no-install-recommends nodejs && rm -rf /var/lib/apt/lists/*)
WORKDIR /app
COPY server.js /app/server.js
COPY index.html /app/public/index.html
COPY settings.yml /etc/searxng/settings.yml
COPY start.sh /start.sh
RUN chmod +x /start.sh
# SearXNG solo escucha dentro del contenedor y con pocos procesos para ahorrar memoria
ENV TRUST_PROXY=1 BIND_ADDRESS=127.0.0.1:8080 GRANIAN_HOST=127.0.0.1 GRANIAN_PORT=8080 GRANIAN_WORKERS=1 UWSGI_WORKERS=1 UWSGI_THREADS=2
ENTRYPOINT ["/start.sh"]
CMD []
