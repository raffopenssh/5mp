.PHONY: build clean stop start restart test

VERSION := $(shell git rev-parse --short HEAD 2>/dev/null || echo "dev")

build:
	@git log --format='%h %ad %s' --date=format:'%Y-%m-%d %H:%M:%S' -20 2>/dev/null | sed 's/[^[:print:]]//g' > .git-commits.txt || echo "" > .git-commits.txt
	go build -ldflags "-X srv.exe.dev/srv.Version=$(VERSION)" -o server ./cmd/srv

clean:
	rm -f server .git-commits.txt

test:
	go test ./...
