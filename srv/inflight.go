package srv

import (
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"
)

// inflightTracker records every request currently inside the handler chain.
// It exists for one reason: when the WAL checkpoint reports `busy`, the
// blocker is a read transaction some connection is holding open, and the
// only way to name it is to know which requests were running at that
// moment. Cost is one map insert/delete per request.
type inflightTracker struct {
	mu   sync.Mutex
	next uint64
	reqs map[uint64]inflightReq
}

type inflightReq struct {
	Method string
	Path   string
	Query  string
	Since  time.Time
}

var inflight = &inflightTracker{reqs: map[uint64]inflightReq{}}

// InflightMiddleware registers the request for the duration of the handler.
func InflightMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := inflight.add(r)
		defer inflight.remove(id)
		next.ServeHTTP(w, r)
	})
}

func (t *inflightTracker) add(r *http.Request) uint64 {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.next++
	t.reqs[t.next] = inflightReq{Method: r.Method, Path: r.URL.Path, Query: r.URL.RawQuery, Since: time.Now()}
	return t.next
}

func (t *inflightTracker) remove(id uint64) {
	t.mu.Lock()
	delete(t.reqs, id)
	t.mu.Unlock()
}

// olderThan returns in-flight requests that started more than d ago, oldest
// first. pwd= is never in the query string here (it is scrubbed by a
// redirect, invariant 14), but strip it anyway before anything is logged.
func (t *inflightTracker) olderThan(d time.Duration) []inflightReq {
	t.mu.Lock()
	defer t.mu.Unlock()
	cut := time.Now().Add(-d)
	var out []inflightReq
	for _, r := range t.reqs {
		if r.Since.Before(cut) {
			r.Query = scrubPwd(r.Query)
			out = append(out, r)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Since.Before(out[j].Since) })
	return out
}

func (t *inflightTracker) count() int {
	t.mu.Lock()
	defer t.mu.Unlock()
	return len(t.reqs)
}

// scrubPwd drops any pwd= parameter from a raw query string.
func scrubPwd(raw string) string {
	if !strings.Contains(raw, "pwd=") {
		return raw
	}
	parts := strings.Split(raw, "&")
	kept := parts[:0]
	for _, p := range parts {
		if !strings.HasPrefix(p, "pwd=") {
			kept = append(kept, p)
		}
	}
	return strings.Join(kept, "&")
}
