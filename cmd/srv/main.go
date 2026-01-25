package main

import (
	"flag"
	"fmt"
	"log/slog"
	"os"

	"srv.exe.dev/srv"
	"srv.exe.dev/srv/areas"
)

var flagListenAddr = flag.String("listen", ":8000", "address to listen on")
var flagDataDir = flag.String("data", "data", "path to data directory")

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

	// Load protected areas from keystones
	dataDir := *flagDataDir
	if store, err := areas.LoadKeystones(dataDir); err == nil {
		server.AreaStore = store
		slog.Info("loaded protected areas", "count", len(store.Areas))
	} else {
		slog.Warn("failed to load areas", "error", err)
	}

	return server.Serve(*flagListenAddr)
}
