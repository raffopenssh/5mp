package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"log/slog"
	"os"

	"srv.exe.dev/srv"
	"srv.exe.dev/srv/areas"
)

func main() {
	syncType := flag.String("type", "both", "Sync type: publications, faolex, or both")
	flag.Parse()

	// Setup logging
	logger := slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{
		Level: slog.LevelInfo,
	}))
	slog.SetDefault(logger)

	// Initialize server with minimal config (just DB and area store)
	server, err := srv.New("db.sqlite3", "localhost:8000")
	if err != nil {
		log.Fatalf("Failed to create server: %v", err)
	}
	defer server.DB.Close()

	// Load protected areas
	store, err := areas.LoadKeystones("data")
	if err != nil {
		log.Fatalf("Failed to load areas: %v", err)
	}
	server.AreaStore = store
	fmt.Printf("Loaded %d protected areas\n", len(store.Areas))

	ctx := context.Background()

	switch *syncType {
	case "publications":
		fmt.Println("Running publication sync...")
		server.RunImprovedPublicationSync(ctx)
	case "faolex":
		fmt.Println("Running FAOLEX sync...")
		server.RunFAOLEXSync(ctx)
	case "both":
		fmt.Println("Running publication sync...")
		server.RunImprovedPublicationSync(ctx)
		fmt.Println("\nRunning FAOLEX sync...")
		server.RunFAOLEXSync(ctx)
	default:
		log.Fatalf("Unknown sync type: %s (use: publications, faolex, or both)", *syncType)
	}

	fmt.Println("\nSync complete!")
}
