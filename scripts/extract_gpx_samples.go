//go:build ignore

package main

import (
	"encoding/xml"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"time"
)

// GPX structures for parsing
type GPXFile struct {
	XMLName xml.Name   `xml:"gpx"`
	Tracks  []GPXTrack `xml:"trk"`
}

type GPXTrack struct {
	Name     string           `xml:"name"`
	Segments []GPXTrackSegment `xml:"trkseg"`
}

type GPXTrackSegment struct {
	Points []GPXPoint `xml:"trkpt"`
}

type GPXPoint struct {
	Lat       float64  `xml:"lat,attr"`
	Lon       float64  `xml:"lon,attr"`
	Elevation *float64 `xml:"ele"`
	Time      *string  `xml:"time"`
}

func main() {
	if len(os.Args) < 2 {
		fmt.Println("Usage: go run extract_gpx_samples.go <gpx_file>")
		os.Exit(1)
	}

	for _, filename := range os.Args[1:] {
		analyzeFile(filename)
	}
}

func analyzeFile(filename string) {
	fmt.Printf("\n=== Analyzing: %s ===\n", filepath.Base(filename))

	file, err := os.Open(filename)
	if err != nil {
		fmt.Printf("Error opening file: %v\n", err)
		return
	}
	defer file.Close()

	// Get file size
	stat, _ := file.Stat()
	fmt.Printf("File size: %.2f MB\n", float64(stat.Size())/(1024*1024))

	// Parse GPX
	data, err := io.ReadAll(file)
	if err != nil {
		fmt.Printf("Error reading file: %v\n", err)
		return
	}

	var gpx GPXFile
	if err := xml.Unmarshal(data, &gpx); err != nil {
		fmt.Printf("Error parsing GPX: %v\n", err)
		return
	}

	totalPoints := 0
	totalSegments := 0
	var firstTime, lastTime *time.Time
	var minLat, maxLat, minLon, maxLon float64 = 90, -90, 180, -180
	var gapCount, largeGapCount int
	var totalDistance float64

	for _, track := range gpx.Tracks {
		for _, seg := range track.Segments {
			totalSegments++
			for i, pt := range seg.Points {
				totalPoints++

				// Update bounds
				if pt.Lat < minLat {
					minLat = pt.Lat
				}
				if pt.Lat > maxLat {
					maxLat = pt.Lat
				}
				if pt.Lon < minLon {
					minLon = pt.Lon
				}
				if pt.Lon > maxLon {
					maxLon = pt.Lon
				}

				// Parse time
				if pt.Time != nil {
					t, err := time.Parse(time.RFC3339, *pt.Time)
					if err == nil {
						if firstTime == nil || t.Before(*firstTime) {
							firstTime = &t
						}
						if lastTime == nil || t.After(*lastTime) {
							lastTime = &t
						}
					}
				}

				// Check for gaps and distance
				if i > 0 {
					prev := seg.Points[i-1]
					dist := haversine(prev.Lat, prev.Lon, pt.Lat, pt.Lon)
					totalDistance += dist

					// Check time gap
					if pt.Time != nil && prev.Time != nil {
						t1, _ := time.Parse(time.RFC3339, *prev.Time)
						t2, _ := time.Parse(time.RFC3339, *pt.Time)
						gap := t2.Sub(t1)
						if gap > 5*time.Minute {
							gapCount++
							speed := dist / gap.Hours()
							if speed > 200 || dist > 10 {
								largeGapCount++
							}
						}
					}
				}
			}
		}
	}

	fmt.Printf("Total tracks: %d\n", len(gpx.Tracks))
	fmt.Printf("Total segments: %d\n", totalSegments)
	fmt.Printf("Total points: %d\n", totalPoints)
	fmt.Printf("Total distance: %.2f km\n", totalDistance)
	fmt.Printf("Bounding box: [%.4f, %.4f] to [%.4f, %.4f]\n", minLon, minLat, maxLon, maxLat)
	if firstTime != nil && lastTime != nil {
		fmt.Printf("Time range: %s to %s\n", firstTime.Format("2006-01-02"), lastTime.Format("2006-01-02"))
		fmt.Printf("Duration: %s\n", lastTime.Sub(*firstTime))
	}
	fmt.Printf("Gaps (>5 min): %d\n", gapCount)
	fmt.Printf("Suspicious gaps (>200km/h or >10km): %d\n", largeGapCount)
}

func haversine(lat1, lon1, lat2, lon2 float64) float64 {
	const earthRadiusKm = 6371.0
	lat1Rad := lat1 * math.Pi / 180
	lat2Rad := lat2 * math.Pi / 180
	deltaLat := (lat2 - lat1) * math.Pi / 180
	deltaLon := (lon2 - lon1) * math.Pi / 180

	a := math.Sin(deltaLat/2)*math.Sin(deltaLat/2) +
		math.Cos(lat1Rad)*math.Cos(lat2Rad)*
			math.Sin(deltaLon/2)*math.Sin(deltaLon/2)
	c := 2 * math.Atan2(math.Sqrt(a), math.Sqrt(1-a))
	return earthRadiusKm * c
}
