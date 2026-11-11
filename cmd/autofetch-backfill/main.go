// autofetch-backfill re-fetches an autofetch source's history from its
// EarthRanger in date windows and files it through the normal upload queue.
// Polite by construction (see srv/autofetch_backfill.go) and resumable:
//
//	go run ./cmd/autofetch-backfill -source 12 -from 2026-06-01 -fetch-only -max 1   # probe one window
//	go run ./cmd/autofetch-backfill -source 12 -from 2026-06-01                      # walk to now
//
// The 5mp service must be running: it is what processes the queue.
package main

import (
	"context"
	"database/sql"
	"flag"
	"fmt"
	"log"
	"os"
	"os/signal"
	"time"

	_ "modernc.org/sqlite"
	"srv.exe.dev/srv"
)

func main() {
	dbPath := flag.String("db", "db.sqlite3", "sqlite database")
	source := flag.Int64("source", 0, "autofetch_sources.id")
	from := flag.String("from", "", "first day, YYYY-MM-DD (UTC)")
	to := flag.String("to", "", "end, YYYY-MM-DD (UTC, exclusive); default now")
	days := flag.Int("window-days", 7, "days per window")
	pause := flag.Float64("pause", 2, "seconds slept before every EarthRanger request")
	state := flag.String("state", "", "state file (default data/autofetch_backfill/source-<id>.json)")
	fetchOnly := flag.Bool("fetch-only", false, "fetch and archive, do not queue")
	maxW := flag.Int("max", 0, "stop after this many windows (0 = all)")
	reset := flag.Bool("reset-cursor", false, "set the source's last_run_at to the end of each processed window even if that moves it back (rebuild from scratch)")
	flag.Parse()
	if *source == 0 || *from == "" {
		flag.Usage()
		os.Exit(2)
	}
	lo, err := time.Parse("2006-01-02", *from)
	if err != nil {
		log.Fatal(err)
	}
	hi := time.Now().UTC().Truncate(time.Minute)
	if *to != "" {
		if hi, err = time.Parse("2006-01-02", *to); err != nil {
			log.Fatal(err)
		}
	}
	if *state == "" {
		*state = fmt.Sprintf("data/autofetch_backfill/source-%d.json", *source)
	}
	db, err := sql.Open("sqlite", *dbPath+"?_pragma=busy_timeout(30000)")
	if err != nil {
		log.Fatal(err)
	}
	defer db.Close()
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	st, err := srv.RunAutofetchBackfill(ctx, db, srv.BackfillOptions{
		SourceID: *source, From: lo, To: hi, WindowDays: *days, Pause: *pause,
		StatePath: *state, FetchOnly: *fetchOnly, MaxWindows: *maxW, ResetCursor: *reset,
		Log: func(f string, a ...any) { fmt.Printf(time.Now().UTC().Format("15:04:05 ")+f+"\n", a...) },
	})
	if st != nil {
		by := map[string]int{}
		pts := 0
		for _, w := range st.Windows {
			by[w.Status]++
			pts += w.Points
		}
		fmt.Printf("windows: %v, %d points\n", by, pts)
	}
	if err != nil {
		log.Fatal(err)
	}
}
