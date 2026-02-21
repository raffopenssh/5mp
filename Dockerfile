# 5MP Conservation Monitoring - Docker Build
FROM golang:1.22-alpine AS builder

WORKDIR /build
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=1 go build -ldflags "-X srv.exe.dev/srv.Version=docker" -o server ./cmd/srv

# Runtime image
FROM alpine:3.19

RUN apk add --no-cache sqlite python3 py3-pip git curl bash && \
    ln -sf python3 /usr/bin/python

WORKDIR /app
COPY --from=builder /build/server /app/server
COPY data/ /app/data/
COPY srv/templates/ /app/srv/templates/
COPY static/ /app/static/

# Python dependencies for fire pipeline
COPY requirements.txt /app/
RUN pip3 install --break-system-packages -r requirements.txt 2>/dev/null || \
    pip3 install -r requirements.txt

COPY scripts/ /app/scripts/
COPY docs/ /app/docs/

EXPOSE 8000
ENV PORT=8000
CMD ["/app/server"]
