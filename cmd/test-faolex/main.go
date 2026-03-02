package main

import (
	"context"
	"fmt"
	"log"
	"log/slog"
	"os"

	"srv.exe.dev/srv"
)

func main() {
	// Setup logging
	logger := slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{
		Level: slog.LevelDebug,
	}))
	slog.SetDefault(logger)

	fmt.Println("Testing FAOLEX scraper...\n")

	// Get a Webshare proxy
	proxy := srv.GetWorkingWebshareProxy("https://www.fao.org/faolex/en/")
	if proxy == "" {
		fmt.Println("WARNING: No Webshare proxy available, using direct connection")
	} else {
		fmt.Printf("Using proxy: %s\n\n", proxy)
	}

	var scraper *srv.FAOLEXScraper
	if proxy != "" {
		scraper = srv.NewFAOLEXScraperWithProxy(proxy)
	} else {
		scraper = srv.NewFAOLEXScraper()
	}

	ctx := context.Background()

	// Test 1: Search for DRC conservation laws
	fmt.Println("Test 1: Searching for DRC conservation laws...")
	params1 := srv.FAOLEXSearchParams{
		Country:  "COD",
		Subjects: []string{"Protected areas", "Wildlife"},
		YearFrom: 2010,
		YearTo:   2024,
	}

	docs1, err := scraper.SearchFAOLEX(ctx, params1)
	if err != nil {
		log.Fatalf("Search failed: %v", err)
	}
	fmt.Printf("Found %d documents\n", len(docs1))
	if len(docs1) > 0 {
		fmt.Printf("First document: %s (%d)\n", docs1[0].Title, docs1[0].Year)
	}

	fmt.Println("\nTest 2: Searching for Virunga-related laws...")
	params2 := srv.FAOLEXSearchParams{
		Country:  "COD",
		Keywords: []string{"Virunga", "national park"},
	}

	docs2, err := scraper.SearchFAOLEX(ctx, params2)
	if err != nil {
		log.Fatalf("Search failed: %v", err)
	}
	fmt.Printf("Found %d documents\n", len(docs2))
	if len(docs2) > 0 {
		for i, doc := range docs2 {
			if i >= 3 {
				break
			}
			fmt.Printf("  - %s (%d)\n", doc.Title, doc.Year)
		}
	}

	fmt.Println("\nTest 3: Searching for Tanzania wildlife laws...")
	params3 := srv.FAOLEXSearchParams{
		Country:  "TZA",
		Subjects: []string{"Wildlife"},
		YearFrom: 2000,
	}

	docs3, err := scraper.SearchFAOLEX(ctx, params3)
	if err != nil {
		log.Fatalf("Search failed: %v", err)
	}
	fmt.Printf("Found %d documents\n", len(docs3))
	if len(docs3) > 0 {
		fmt.Printf("Sample: %s (%d)\n", docs3[0].Title, docs3[0].Year)
	}

	fmt.Println("\n✓ FAOLEX scraper is working!")
}
