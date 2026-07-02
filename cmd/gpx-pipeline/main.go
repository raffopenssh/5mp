// gpx-pipeline runs the FULL production classification pipeline on GPX files:
// ParseGPX → SplitIntoSegments (30-min windows + hints) → RemoveStraightLineGaps
// → ValidateAndClassifyGPX (classify + merge) → majority vote (what persistUpload stores).
//
// Usage: go run ./cmd/gpx-pipeline [-nohints] [-v] file.gpx...
package main

import (
	"flag"
	"fmt"
	"os"

	"srv.exe.dev/srv"
	"srv.exe.dev/srv/gpx"
)

func main() {
	noHints := flag.Bool("nohints", false, "strip track names/ER metadata/waypoints (movement only)")
	verbose := flag.Bool("v", false, "print per-classified-segment detail")
	flag.Parse()

	for _, path := range flag.Args() {
		f, err := os.Open(path)
		if err != nil {
			fmt.Printf("%s: %v\n", path, err)
			continue
		}
		data, err := gpx.ParseGPX(f)
		f.Close()
		if err != nil {
			fmt.Printf("%s: parse error: %v\n", path, err)
			continue
		}

		if *noHints {
			data.Waypoints = nil
			for i := range data.Tracks {
				data.Tracks[i].Name = ""
				data.Tracks[i].Activity = ""
				data.Tracks[i].ERSubjectType = ""
				data.Tracks[i].ERSubjectSubtype = ""
				data.Tracks[i].ERPatrolType = ""
			}
		}

		segments := gpx.SplitIntoSegments(data, 0)
		segments = gpx.RemoveStraightLineGaps(segments)
		res := srv.ValidateAndClassifyGPX(segments)

		// Majority vote exactly as persistUpload does, over include-in-effort
		// classified segments mapped back to original segments.
		typeDistKm := map[string]float64{}
		subDistKm := map[string]float64{}
		for _, cs := range res.ClassifiedSegments {
			if !cs.IncludeInEffort {
				continue
			}
			for _, origIdx := range cs.OriginalIndices {
				if origIdx < len(segments) {
					if cs.MovementType != "" {
						typeDistKm[cs.MovementType] += segments[origIdx].DistanceKm
					}
					if cs.MovementSubtype != "" {
						subDistKm[cs.MovementSubtype] += segments[origIdx].DistanceKm
					}
				}
			}
		}
		majority, sub := "foot", ""
		var maxD float64
		for mt, d := range typeDistKm {
			if d > maxD {
				maxD, majority = d, mt
			}
		}
		maxD = 0
		for st, d := range subDistKm {
			if d > maxD {
				maxD, sub = d, st
			}
		}

		fmt.Printf("%-58s → %-8s sub=%-10s | patrol=%.1fkm road=%.1f bnd=%.1f excl=%.1f | foot=%.1f veh=%.1f air=%.1f | csegs=%d\n",
			path, majority, sub,
			res.PatrolKm, res.RoadKm, res.BoundaryKm, res.ExcludedKm,
			res.MovementStats.FootKm, res.MovementStats.VehicleKm, res.MovementStats.AircraftKm,
			len(res.ClassifiedSegments))

		if *verbose {
			for i, cs := range res.ClassifiedSegments {
				fmt.Printf("   cs%02d %-14s mt=%-8s sub=%-10s act=%-8s %.1fkm avg=%.1fkm/h effort=%v orig=%v  %s\n",
					i, cs.Classification, cs.MovementType, cs.MovementSubtype, cs.ActivityType,
					cs.DistanceKm, cs.AvgSpeedKmh, cs.IncludeInEffort, cs.OriginalIndices, cs.Reason)
			}
		}
	}
}
