// One-off tool: register a suspected mining site as a settlement candidate.
//
//	go run ./cmd/register-mining -park CAF_Chinko -lat 7.44637 -lon 24.02954 \
//	   -area 5000 -note "Confirmed artisanal gold mine ..."
package main

import (
	"flag"
	"fmt"
	"os"

	"srv.exe.dev/srv"
)

func main() {
	park := flag.String("park", "", "park id")
	lat := flag.Float64("lat", 0, "latitude")
	lon := flag.Float64("lon", 0, "longitude")
	area := flag.Float64("area", 0, "area m2")
	note := flag.String("note", "", "narrative prefix")
	flag.Parse()
	if *park == "" || *lat == 0 {
		flag.Usage()
		os.Exit(2)
	}
	server, err := srv.New("db.sqlite3", "register-mining")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	id, err := server.RegisterMiningCandidate(*park, *lat, *lon, *area, *note)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Println("settlement id:", id)
}
