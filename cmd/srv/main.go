package main

import (
	"flag"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"

	"srv.exe.dev/srv"
	"srv.exe.dev/srv/areas"
)

var flagListenAddr = flag.String("listen", ":8000", "address to listen on")
var flagAreasFile = flag.String("areas", "data/areas.json", "path to areas JSON file")

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
	}
}

func run() error {
	flag.Parse()
	hostname, err := os.Hostname()
	if err != nil {
		hostname = "unknown"
	}
	server, err := srv.New("db.sqlite3", hostname)
	if err != nil {
		return fmt.Errorf("create server: %w", err)
	}

	// Load protected areas if file exists
	areasPath := *flagAreasFile
	if !filepath.IsAbs(areasPath) {
		// Try relative to working directory
		if _, err := os.Stat(areasPath); err == nil {
			if store, err := areas.LoadAreas(areasPath); err == nil {
				server.AreaStore = store
				slog.Info("loaded protected areas", "count", len(store.Areas), "path", areasPath)
			} else {
				slog.Warn("failed to load areas", "error", err)
			}
		} else {
			slog.Info("no areas file found, skipping", "path", areasPath)
		}
	}

	return server.Serve(*flagListenAddr)
}
