package srv

import (
	"net/http"
	"strings"
)

// isDownloadStart reports whether this request begins a download, as opposed
// to resuming one. A client on a flaky link fetches one file in many Range
// requests; counting each 206 as a "download" inflates the stat and, worse,
// makes a resumed transfer look like N downloads. A request with no Range
// header, or one whose range starts at byte 0, is the start.
func isDownloadStart(r *http.Request) bool {
	rng := strings.ReplaceAll(r.Header.Get("Range"), " ", "")
	return rng == "" || strings.HasPrefix(rng, "bytes=0-")
}
