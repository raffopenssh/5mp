package srv

import (
	"net/http"
	"strings"
	"sync"
	"time"
)

// rateLimiter is a simple per-IP token bucket rate limiter.
type rateLimiter struct {
	mu       sync.Mutex
	buckets  map[string]*bucket
	rate     int           // tokens per interval
	interval time.Duration // refill interval
	burst    int           // max tokens
}

type bucket struct {
	tokens   int
	lastTime time.Time
}

func newRateLimiter(rate int, interval time.Duration, burst int) *rateLimiter {
	rl := &rateLimiter{
		buckets:  make(map[string]*bucket),
		rate:     rate,
		interval: interval,
		burst:    burst,
	}
	// Cleanup stale entries every 5 minutes
	go func() {
		for {
			time.Sleep(5 * time.Minute)
			rl.cleanup()
		}
	}()
	return rl
}

func (rl *rateLimiter) allow(key string) bool {
	rl.mu.Lock()
	defer rl.mu.Unlock()

	now := time.Now()
	b, ok := rl.buckets[key]
	if !ok {
		rl.buckets[key] = &bucket{tokens: rl.burst - 1, lastTime: now}
		return true
	}

	// Refill tokens based on elapsed time
	elapsed := now.Sub(b.lastTime)
	refill := int(elapsed / rl.interval) * rl.rate
	if refill > 0 {
		b.tokens += refill
		if b.tokens > rl.burst {
			b.tokens = rl.burst
		}
		b.lastTime = now
	}

	if b.tokens <= 0 {
		return false
	}
	b.tokens--
	return true
}

func (rl *rateLimiter) cleanup() {
	rl.mu.Lock()
	defer rl.mu.Unlock()
	cutoff := time.Now().Add(-10 * time.Minute)
	for k, b := range rl.buckets {
		if b.lastTime.Before(cutoff) {
			delete(rl.buckets, k)
		}
	}
}

func extractIP(r *http.Request) string {
	if fwd := r.Header.Get("X-Forwarded-For"); fwd != "" {
		// Take only the first IP (the original client)
		if first, _, ok := strings.Cut(fwd, ","); ok {
			return strings.TrimSpace(first)
		}
		return strings.TrimSpace(fwd)
	}
	// Strip port from RemoteAddr
	host := r.RemoteAddr
	for i := len(host) - 1; i >= 0; i-- {
		if host[i] == ':' {
			return host[:i]
		}
	}
	return host
}

// RateLimitMiddleware wraps a handler with per-IP rate limiting.
func RateLimitMiddleware(rl *rateLimiter, next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		ip := extractIP(r)
		if !rl.allow(ip) {
			http.Error(w, "Too many requests", http.StatusTooManyRequests)
			return
		}
		next(w, r)
	}
}
