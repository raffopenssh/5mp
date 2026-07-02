// gpx-classify is a debugging CLI that runs the movement classifier on GPX files.
// Usage: go run ./cmd/gpx-classify [-hints] file1.gpx [file2.gpx ...]
// By default it classifies using MOVEMENT ONLY (no name/waypoint/extension hints).
package main

import (
	"flag"
	"fmt"
	"os"

	"srv.exe.dev/srv/gpx"
)

func main() {
	useHints := flag.Bool("hints", false, "use name/waypoint/ER hints (default: movement only)")
	perSegment := flag.Bool("segments", false, "show per-segment classification")
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

		for ti, trk := range data.Tracks {
			var pts []gpx.Point
			for _, s := range trk.Segments {
				pts = append(pts, s...)
			}
			if len(pts) < 3 {
				fmt.Printf("%-55s trk%d: only %d pts, skipped\n", path, ti, len(pts))
				continue
			}
			hint := gpx.MovementHint{}
			if *useHints {
				hint = gpx.ExtractMovementHintsFromWaypoints(data.Waypoints)
			}
			c := gpx.ClassifyMovementFullWithHint(pts, hint)
			m := c.Metrics
			fmt.Printf("%-55s %-8s sub=%-10s act=%-8s conf=%.2f | avg=%.1f p90=%.1f max=%.1f km/h dist=%.1fkm dur=%.0fmin stops=%.2f cv=%.2f lin=%.2f bvar=%.2f elev[has=%v rng=%.0f max=%.0f] climb[avg=%.2f max=%.2f] roll[to=%.0fm@%.1f ld=%.0fm@%.1f] hover=%.2f iv=%.0fs\n",
				path, c.MovementType, c.MovementSubtype, c.ActivityType, c.Confidence,
				m.AvgSpeedKmh, m.P90SpeedKmh, m.MaxSpeedKmh, m.TotalDistanceKm, m.DurationMinutes,
				m.StopFrequency, m.SpeedCV, m.LinearityScore, m.BearingVariance,
				m.HasElevation, m.ElevationRangeM, m.MaxElevationM,
				m.AvgClimbRateMps, m.MaxClimbRateMps,
				m.TakeoffRollM, m.TakeoffAccelKmhs, m.LandingRollM, m.LandingDecelKmhs,
				m.HoverRatio, m.MedianIntervalSec)

			if *perSegment {
				segs := gpx.SplitIntoSegments(data, 0)
				for si, sg := range segs {
					fmt.Printf("    seg%02d %-8s sub=%-10s pts=%d avg=%.1f dist=%.1fkm\n",
						si, sg.MovementType, sg.MovementSubtype, len(sg.Points), sg.AvgSpeedKmh, sg.DistanceKm)
				}
			}
		}
	}
}
