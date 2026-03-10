package srv

import (
	"log/slog"
	"net/http"
)

// internalError logs the real error and returns a generic message to the client.
func internalError(w http.ResponseWriter, msg string, err error) {
	slog.Error(msg, "error", err)
	http.Error(w, "Internal server error", http.StatusInternalServerError)
}
