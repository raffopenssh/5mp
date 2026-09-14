package srv

import (
	"net/url"
	"testing"
)

func TestForwardQueryCarriesSectionNeverPwd(t *testing.T) {
	q := url.Values{"methods": {"fire"}, "pwd": {"secret"}, "layers": {"pixels"}}
	got := forwardQuery("/?layers=fires", q)
	u, _ := url.Parse(got)
	if u.Query().Get("methods") != "fire" {
		t.Fatalf("methods not forwarded: %s", got)
	}
	if u.Query().Get("pwd") != "" {
		t.Fatalf("pwd forwarded: %s", got)
	}
	if u.Query().Get("layers") != "fires" {
		t.Fatalf("target's own param overridden: %s", got)
	}
	if forwardQuery("/", nil) != "/" {
		t.Fatal("empty query must leave target untouched")
	}
}
