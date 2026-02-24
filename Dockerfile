# 5MP Conservation Monitoring - Docker Runtime
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 python3 python3-pip git curl ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    ln -sf python3 /usr/bin/python

WORKDIR /app

# Copy pre-built server binary
COPY server /app/server

# Copy templates and static files
COPY srv/templates/ /app/srv/templates/
COPY srv/static/ /app/srv/static/

# Scripts and docs (Python deps installed at runtime if needed)
COPY scripts/ /app/scripts/
COPY docs/ /app/docs/

EXPOSE 8000
ENV PORT=8000
CMD ["/app/server"]
