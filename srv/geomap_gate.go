package srv

import (
	"encoding/json"
	"net/http"
)

// Geology is withheld from the shared demo password.
//
// The geology sheets are the layer whose terms are thinnest: the CAR sheet is
// an in-copyright map used as research/study material, and the affinity model
// on top of it points at rock that hosts gold and diamonds. Behind a password
// that is printed in the README, "research use" is not an honest description
// of the audience. So the layer exists only for the tenants that hold a real
// password, and for guest links minted by them (a guest capability reads in
// its issuer's tenant -- RequestEnv already says so). The sandbox tenant gets
// a catalogue that says WHY it is empty, and 403 on everything else, so a
// demo reader sees a closed door with a sign rather than a bug.
//
// One gate, applied at the mux to every /api/geomap* route, rather than a
// check per handler: the last time a rule lived in each handler separately,
// one of them was missed.
const geoMapWithheldReason = "Geology layers are available to project partners and through links they share, not on the public demo password. Their source sheets are used under research-use terms (see /licenses)."

func geoMapWithheld(r *http.Request) bool { return RequestEnv(r) == sandboxTenant }

func geoMapGate(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if geoMapWithheld(r) {
			w.Header().Set("Content-Type", "application/json")
			w.Header().Set("Cache-Control", "private, no-store")
			w.WriteHeader(http.StatusForbidden)
			json.NewEncoder(w).Encode(map[string]any{"error": "geology withheld", "reason": geoMapWithheldReason})
			return
		}
		next(w, r)
	}
}
