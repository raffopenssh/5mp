package srv

import (
	"net/http"
	"regexp"
	"strings"
)

// parkIDRe validates park identifiers: 3-letter ISO country prefix + name.
// Name allows unicode letters (Léfini, Campo-Ma'an), digits, _ - ' .
var parkIDRe = regexp.MustCompile(`^[A-Z]{3}_[\pL0-9_'\-.]+$`)

// ValidParkID reports whether s looks like a legitimate park identifier.
// Rejects path traversal and other injection attempts before s reaches
// file paths or subprocess arguments.
func ValidParkID(s string) bool {
	return len(s) <= 80 && parkIDRe.MatchString(s)
}

// ParkIDMiddleware rejects requests whose park identifier (path segment
// after /api/parks/ or /api/park/, or ?park= query param) is malformed.
func ParkIDMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		for _, prefix := range []string{"/api/parks/", "/api/park/"} {
			if rest, ok := strings.CutPrefix(r.URL.Path, prefix); ok {
				id, _, _ := strings.Cut(rest, "/")
				if id != "" && !ValidParkID(id) {
					http.Error(w, "invalid park id", http.StatusBadRequest)
					return
				}
			}
		}
		if p := r.URL.Query().Get("park"); p != "" && !ValidParkID(p) {
			http.Error(w, "invalid park id", http.StatusBadRequest)
			return
		}
		next.ServeHTTP(w, r)
	})
}
