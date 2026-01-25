package main

import (
	"context"
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

	// Load WDPA index for searching all African PAs
	wdpaPath := dataDir + "/wdpa_index.json"
	if wdpaIndex, err := areas.LoadWDPAIndex(wdpaPath); err == nil {
		server.WDPAIndex = wdpaIndex
		slog.Info("loaded WDPA index", "count", len(wdpaIndex.Entries))
	} else {
		slog.Warn("failed to load WDPA index", "error", err)
	}

	// Load legal frameworks
	legalPath := dataDir + "/legal_frameworks.json"
	if legalStore, err := srv.LoadLegalFrameworks(legalPath); err == nil {
		server.LegalStore = legalStore
		slog.Info("loaded legal frameworks", "countries", len(legalStore.Frameworks.Countries), "pa_specific", len(legalStore.Frameworks.PASpecific))
	} else {
		slog.Warn("failed to load legal frameworks", "error", err)
	}

	// Start research publication worker in background
	ctx := context.Background()
	go server.StartResearchWorker(ctx)

	return server.Serve(*flagListenAddr)
}
