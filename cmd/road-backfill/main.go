// road-backfill replays deduplicated vehicle_tracks through the road learner
// to seed learned_roads. Intended to run ONCE on an empty learned_roads table
// (subsequent incremental learning happens inside GPX learner jobs).
package main

import (
	"context"
	"database/sql"
	"flag"
	"fmt"
	"log"
	"os"

	_ "modernc.org/sqlite"
	"srv.exe.dev/srv"
)

func main() {
	dbPath := flag.String("db", "db.sqlite3", "path to sqlite database")
	force := flag.Bool("force", false, "run even if learned_roads is not empty")
	flag.Parse()

	db, err := sql.Open("sqlite", *dbPath+"?_pragma=busy_timeout(10000)&_pragma=foreign_keys(1)")
	if err != nil {
		log.Fatal(err)
	}
	defer db.Close()

	var n int
	if err := db.QueryRow("SELECT COUNT(*) FROM learned_roads").Scan(&n); err != nil {
		log.Fatal(err)
	}
	if n > 0 && !*force {
		fmt.Fprintf(os.Stderr, "learned_roads has %d rows; backfill would double-count traversals. Use -force to override.\n", n)
		os.Exit(1)
	}

	learner := srv.NewGPXLearner(db)
	if err := learner.BackfillLearnedRoads(context.Background()); err != nil {
		log.Fatal(err)
	}

	var roads, twoPlus int
	db.QueryRow("SELECT COUNT(*), SUM(match_count >= 2) FROM learned_roads").Scan(&roads, &twoPlus)
	fmt.Printf("learned_roads: %d rows (%d with >=2 traversals)\n", roads, twoPlus)
}
