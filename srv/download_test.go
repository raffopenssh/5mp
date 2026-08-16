package srv

import (
	"net/http/httptest"
	"testing"
)

// A resumed transfer is one download, not N. Every Range chunk after the
// first must not bump the download counter (clients in low-bandwidth regions
// fetch a 200 MB file in dozens of chunks).
func TestIsDownloadStart(t *testing.T) {
	cases := []struct {
		rng  string
		want bool
	}{
		{"", true},
		{"bytes=0-", true},
		{"bytes=0-1023", true},
		{"bytes = 0-1023", true},
		{"bytes=1024-", false},     // resume
		{"bytes=52428800-", false}, // resume mid-file
		{"bytes=-500", false},      // suffix probe
	}
	for _, c := range cases {
		r := httptest.NewRequest("GET", "/api/geopackage/x/download", nil)
		if c.rng != "" {
			r.Header.Set("Range", c.rng)
		}
		if got := isDownloadStart(r); got != c.want {
			t.Errorf("Range %q: got %v, want %v", c.rng, got, c.want)
		}
	}
}
