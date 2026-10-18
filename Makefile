.PHONY: build clean stop start restart test gif

VERSION := $(shell git rev-parse --short HEAD 2>/dev/null || echo "dev")

build:
	@git log --format='%h %ad %s' --date=format:'%Y-%m-%d %H:%M:%S' -20 2>/dev/null | sed 's/[^[:print:]]//g' > .git-commits.txt || echo "" > .git-commits.txt
	go build -ldflags "-X srv.exe.dev/srv.Version=$(VERSION)" -o server ./cmd/srv

clean:
	rm -f server .git-commits.txt

test:
	go test ./...

# Headless GIF of the time animator (docs/agents/animator.md "GIF export"):
#   make gif URL='/?aoi_focus=XSA_Study_Area&from=2025-10-01&to=2026-03-31&anim=fireGrid,trajs&anim_speed=10' OUT=out.gif
GIF_ARGS ?=
gif:
	@test -n "$(URL)" -a -n "$(OUT)" || { echo "usage: make gif URL='<path+query with anim=>' OUT=out.gif [GIF_ARGS='--palette global']"; exit 2; }
	python3 scripts/gif_export.py '$(URL)' '$(OUT)' $(GIF_ARGS)
