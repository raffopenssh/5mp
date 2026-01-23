.PHONY: build clean stop start restart test

build:
	go build -o server ./cmd/srv

clean:
	rm -f server

test:
	go test ./...
