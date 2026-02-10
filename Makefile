.PHONY: build clean stop start restart test

VERSION := $(shell git rev-parse --short HEAD 2>/dev/null || echo "dev")

build:
	go build -ldflags "-X srv.exe.dev/srv.Version=$(VERSION)" -o server ./cmd/srv

clean:
	rm -f server

test:
	go test ./...
