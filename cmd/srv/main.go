package main

import (
	"context"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"srv.exe.dev/srv"
	"srv.exe.dev/srv/areas"
)

var flagListenAddr = flag.String("listen", ":8000", "address to listen on")
var flagDataDir = flag.String("data", "data", "path to data directory")

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
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
		if server.GPXLearner != nil {
			server.GPXLearner.SetAreaStore(store)
		}
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

	// Load GADM data for country/region search
	gadmPath := dataDir + "/gadm_africa.json"
	if gadmStore, err := srv.LoadGADMStore(gadmPath); err == nil {
		server.GADMStore = gadmStore
		slog.Info("loaded GADM data", "countries", len(gadmStore.Countries), "regions", len(gadmStore.Regions))
	} else {
		slog.Warn("failed to load GADM data", "error", err)
	}

	// Load WorldClim precipitation data for grid cells
	worldclimPath := dataDir + "/worldclim/grid_precip.json"
	if err := srv.LoadWorldClimData(worldclimPath); err == nil {
		slog.Info("loaded WorldClim grid precipitation data")
	} else {
		slog.Warn("failed to load WorldClim data", "error", err)
	}

	// Create cancellable context for background workers
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// Start background workers with cancellable context
	go server.StartResearchWorker(ctx)
	go server.StartNarrativeCacheWorker(ctx)
	go server.StartAutofetchWorker(ctx)
	go server.StartUploadQueueCleanup(ctx)
	go server.StartWALCheckpointWorker(ctx, "db.sqlite3")
	server.StartTurbidityWatcher()

	// Start HTTP server in a goroutine
	errCh := make(chan error, 1)
	go func() {
		errCh <- server.Serve(*flagListenAddr)
	}()

	// Wait for interrupt signal or server error
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)

	select {
	case sig := <-sigCh:
		slog.Info("received signal, shutting down", "signal", sig)
	case err := <-errCh:
		if err != nil {
			return fmt.Errorf("server error: %w", err)
		}
	}

	// Cancel background workers
	cancel()

	// Gracefully shutdown HTTP server
	if err := server.Shutdown(); err != nil {
		slog.Warn("shutdown error", "error", err)
	}

	slog.Info("server stopped")
	return nil
}
