package srv

import (
	"sync"
	"time"
)

// The satellite fleet behind the fire archive (F11).
//
// One VIIRS sensor flies before 2024-01 (Suomi-NPP, `N`); three fly after
// (`N`, `N20`, `N21`). Every raw detection chart therefore has a ~3x step at
// that date which is instrument, not landscape — CAF_Chinko goes 61,509
// detections in 2023 to 203,223 in 2024 without a single extra hectare
// burning. Two adjacent points that measure different quantities must not be
// joined by an unbroken line (invariant 7); this is the same failure as F8's
// Hansen-to-GFW switch, and it reuses the sparkline's `d.brk` mechanism.
//
// The fleet is MEASURED, never typed: `fire_sensor_epochs` is written by
// scripts/build_sensor_epochs.py from the archive itself
// (db/migrations/056-fire-sensor-epochs.sql), because "three sensors since
// 2024" describes an ingest history that grows nightly and the next sensor
// would move it with no code change (invariant 2).
//
// An EMPTY table means the fleet is UNMEASURED, and that is not the same as
// "the fleet never changed" (invariant 12). Readers get
// `sensor_epochs_measured: false` and draw no breaks, rather than an unbroken
// line that quietly asserts continuity.

type sensorEpoch struct {
	Sensors    string `json:"sensors"`
	Count      int    `json:"sensor_count"`
	Detections int    `json:"detections"`
}

type sensorEpochTable struct {
	mu       sync.RWMutex
	byMonth  map[string]sensorEpoch
	loadedAt time.Time
}

var sensorEpochs sensorEpochTable

// SensorEpochs returns month ('YYYY-MM') -> fleet, cached for an hour. The
// table is rewritten monthly by a script, so a stale hour cannot mislead.
func (s *Server) SensorEpochs() map[string]sensorEpoch {
	sensorEpochs.mu.RLock()
	if sensorEpochs.byMonth != nil && time.Since(sensorEpochs.loadedAt) < time.Hour {
		m := sensorEpochs.byMonth
		sensorEpochs.mu.RUnlock()
		return m
	}
	sensorEpochs.mu.RUnlock()

	m := map[string]sensorEpoch{}
	rows, err := s.DB.Query(`SELECT month, sensors, sensor_count, detections FROM fire_sensor_epochs`)
	if err == nil {
		for rows.Next() {
			var mo string
			var e sensorEpoch
			if rows.Scan(&mo, &e.Sensors, &e.Count, &e.Detections) == nil {
				m[mo] = e
			}
		}
		rows.Close()
	}
	// A failed read must not be cached as "no fleet ever measured": leave the
	// previous table in place and retry on the next call.
	if err != nil || len(m) == 0 {
		sensorEpochs.mu.RLock()
		defer sensorEpochs.mu.RUnlock()
		return sensorEpochs.byMonth
	}
	sensorEpochs.mu.Lock()
	sensorEpochs.byMonth, sensorEpochs.loadedAt = m, time.Now()
	sensorEpochs.mu.Unlock()
	return m
}

// sensorsOn returns the fleet flying on a 'YYYY-MM-DD' date, and whether it is
// measured at all. Unmeasured is a distinct answer from unchanged.
func sensorsOn(epochs map[string]sensorEpoch, date string) (string, int, bool) {
	if len(epochs) == 0 || len(date) < 7 {
		return "", 0, false
	}
	e, ok := epochs[date[:7]]
	if !ok {
		return "", 0, false
	}
	return e.Sensors, e.Count, true
}
