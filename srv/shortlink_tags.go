package srv

// Purpose tags on short links.
//
// A tag is BOOKKEEPING, NOT A GRANT: setting, renaming or clearing one never
// remints a key, never widens what it shows and never changes when it dies.
// What it does is answer "which links did I hand out for this report / this
// workshop / this ministry", so that the whole group can be renewed, audited
// or revoked as one thing.
//
// A LINK CARRIES A SET OF TAGS, NOT ONE.
// Migration 060 stored a single `short_links.tag`, which quietly made the two
// truthful answers exclusive: a link cited in a report AND handed out at a
// workshop had to pick, and picking took it out of the next "renew #report" —
// the exact accident tags exist to prevent. Since 061 the pairs live in
// `short_link_tags` (slug, tag) and the old column is gone rather than kept in
// sync, because a duplicated fact with two writers drifts (AGENTS.md
// invariant 5) and a reader selecting the dead column would see the first tag
// as if it were the only one.
//
// Every read and write here is scoped to the caller's own links through
// shortLinkOwned / shortCallerRef: a tag is one name for one purpose *per
// login*, and one tenant must not be able to read or rewrite another's
// bookkeeping (see srv/shortlink.go, "SCOPED TO THE CALLER").

import (
	"database/sql"
	"net/http"
	"regexp"
	"sort"
	"strings"
	"time"
)

// shortTagRe: a tag names a purpose ("report"), so it is a slug, not prose.
var shortTagRe = regexp.MustCompile(`[^a-z0-9_-]+`)

// shortMaxTagsPerLink: a set, but a readable one. Past a handful of chips the
// row stops being scannable and the tag stops being a group — and an unbounded
// set on a row nobody re-reads is a way to make the table grow per click.
const shortMaxTagsPerLink = 8

func shortSanitizeTag(t string) string {
	t = shortTagRe.ReplaceAllString(strings.ToLower(strings.TrimSpace(t)), "")
	t = strings.Trim(t, "-_")
	if len(t) > 32 {
		t = t[:32]
	}
	return t
}

// shortSanitizeTags cleans a requested set: sanitise each, drop empties, dedupe
// (keeping the caller's order — the first tag is the one chips lead with), cap.
func shortSanitizeTags(in []string) []string {
	out := []string{}
	seen := map[string]bool{}
	for _, raw := range in {
		t := shortSanitizeTag(raw)
		if t == "" || seen[t] {
			continue
		}
		seen[t] = true
		out = append(out, t)
		if len(out) >= shortMaxTagsPerLink {
			break
		}
	}
	return out
}

// shortTagsOf returns one link's tags, alphabetical so two surfaces showing
// the same link show the same order (AGENTS.md invariant 7).
func (s *Server) shortTagsOf(slug string) []string {
	rows, err := s.DB.Query(`SELECT tag FROM short_link_tags WHERE slug = ? ORDER BY tag`, slug)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var t string
		if rows.Scan(&t) == nil {
			out = append(out, t)
		}
	}
	return out
}

// shortTagsByOwner loads every (slug → tags) pair for one login in ONE query.
// The list handler must not run a query per row: the sheet shows up to 1000
// links, and N+1 here is how a sheet becomes a spinner.
func (s *Server) shortTagsByOwner(ref string) map[string][]string {
	out := map[string][]string{}
	if ref == "" {
		return out
	}
	rows, err := s.DB.Query(`SELECT t.slug, t.tag FROM short_link_tags t
		JOIN short_links l ON l.slug = t.slug
		WHERE l.pwd_ref = ? ORDER BY t.slug, t.tag`, ref)
	if err != nil {
		return out
	}
	defer rows.Close()
	for rows.Next() {
		var slug, tag string
		if rows.Scan(&slug, &tag) == nil {
			out[slug] = append(out[slug], tag)
		}
	}
	return out
}

// shortSetTags replaces a link's whole tag set. Written as delete-then-insert
// inside one transaction: a half-applied set is a link that is in neither group.
func (s *Server) shortSetTags(slug string, tags []string) error {
	tx, err := s.DB.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err := tx.Exec(`DELETE FROM short_link_tags WHERE slug = ?`, slug); err != nil {
		return err
	}
	now := time.Now().UTC().Format(time.RFC3339)
	for _, t := range tags {
		if _, err := tx.Exec(`INSERT OR IGNORE INTO short_link_tags (slug, tag, created_at)
			VALUES (?, ?, ?)`, slug, t, now); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// shortAddTags adds without removing what is already there — the "# add tag"
// gesture. Returns the resulting set.
func (s *Server) shortAddTags(slug string, tags []string) error {
	now := time.Now().UTC().Format(time.RFC3339)
	have := len(s.shortTagsOf(slug))
	for _, t := range tags {
		if have >= shortMaxTagsPerLink {
			break
		}
		if _, err := s.DB.Exec(`INSERT OR IGNORE INTO short_link_tags (slug, tag, created_at)
			VALUES (?, ?, ?)`, slug, t, now); err != nil {
			return err
		}
		have++
	}
	return nil
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

// tagsResponse is what every tag mutation answers with: the link and the set
// it now carries. The UI never guesses the outcome of its own request — the
// server sanitises, dedupes and caps, so only its answer is the truth.
func (s *Server) writeTagsResponse(w http.ResponseWriter, slug string) {
	tags := s.shortTagsOf(slug)
	if tags == nil {
		tags = []string{}
	}
	first := ""
	if len(tags) > 0 {
		first = tags[0]
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"slug": slug,
		"tags": tags,
		// `tag` stays in the response as the FIRST tag, for the one-line
		// callers (a gpkg card, an old script) that only ever want a word.
		"tag": first,
	})
}

// HandleAPIShortLinkRetag — POST /api/shortlink/{slug}/retag
//
//	{tags:[…]}   replace the whole set   (the canonical form)
//	{add:"x"}    add one, keep the rest  (the "# add tag" chip)
//	{remove:"x"} drop one, keep the rest (a chip's ×)
//	{tag:"x"}    legacy: same as {tags:["x"]}; {tag:""} clears every tag
//
// Aliases carry no tags (they are ghosts of a rename, not links), so tagging
// one is refused rather than silently accepted.
func (s *Server) HandleAPIShortLinkRetag(w http.ResponseWriter, r *http.Request) {
	slug := strings.ToLower(strings.Trim(r.PathValue("slug"), "/"))
	var body struct {
		Tag    *string  `json:"tag"`
		Tags   []string `json:"tags"`
		Add    string   `json:"add"`
		Remove string   `json:"remove"`
	}
	if err := decodeJSONBody(r, &body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "bad request"})
		return
	}
	if !s.shortLinkOwned(slug, r) {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "no such link"})
		return
	}
	var alias sql.NullString
	if err := s.DB.QueryRow(`SELECT alias_of FROM short_links WHERE slug = ?`, slug).Scan(&alias); err != nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "no such link"})
		return
	}
	if alias.Valid && alias.String != "" {
		writeJSON(w, http.StatusConflict, map[string]string{"error": "an alias carries no tag"})
		return
	}

	// "The caller typed something and sanitising ate all of it" must be an
	// error, not a silent no-op: accepting it would store nothing while the
	// user believes a tag was set (AGENTS.md invariant 1).
	badWord := func(raw string) bool {
		return strings.TrimSpace(raw) != "" && shortSanitizeTag(raw) == ""
	}
	switch {
	case body.Add != "":
		if badWord(body.Add) {
			writeJSON(w, http.StatusBadRequest, map[string]string{
				"error": "a tag uses lowercase letters, digits, - and _"})
			return
		}
		if err := s.shortAddTags(slug, shortSanitizeTags([]string{body.Add})); err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "could not retag"})
			return
		}
	case body.Remove != "":
		t := shortSanitizeTag(body.Remove)
		if t == "" {
			writeJSON(w, http.StatusBadRequest, map[string]string{
				"error": "a tag uses lowercase letters, digits, - and _"})
			return
		}
		if _, err := s.DB.Exec(`DELETE FROM short_link_tags WHERE slug = ? AND tag = ?`, slug, t); err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "could not retag"})
			return
		}
	case body.Tags != nil:
		for _, raw := range body.Tags {
			if badWord(raw) {
				writeJSON(w, http.StatusBadRequest, map[string]string{
					"error": "a tag uses lowercase letters, digits, - and _"})
				return
			}
		}
		if err := s.shortSetTags(slug, shortSanitizeTags(body.Tags)); err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "could not retag"})
			return
		}
	case body.Tag != nil:
		if badWord(*body.Tag) {
			writeJSON(w, http.StatusBadRequest, map[string]string{
				"error": "a tag uses lowercase letters, digits, - and _"})
			return
		}
		if err := s.shortSetTags(slug, shortSanitizeTags([]string{*body.Tag})); err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "could not retag"})
			return
		}
	default:
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "nothing to change"})
		return
	}
	s.writeTagsResponse(w, slug)
}

// HandleAPIShortLinkRetagAll — POST /api/shortlinks/retag {tag, new_tag}
//
// Renames a purpose tag EVERYWHERE it appears in the caller's links. A tag is
// one name for one purpose, so renaming it on a single row would fork the group
// and quietly exempt the others from the next renewal the new name gets.
//
// An empty new_tag DELETES the tag everywhere (the group-level ×). The response
// carries the count so the UI can say what actually moved — a no-op must not
// read as an answer (invariant 1), so 0 renamed is reported as 0.
func (s *Server) HandleAPIShortLinkRetagAll(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Tag    string `json:"tag"`
		NewTag string `json:"new_tag"`
		Delete bool   `json:"delete"`
	}
	if err := decodeJSONBody(r, &body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "bad request"})
		return
	}
	old := shortSanitizeTag(body.Tag)
	newT := shortSanitizeTag(body.NewTag)
	ref := shortCallerRef(r)
	if old == "" || ref == "" || (newT == "" && !body.Delete) {
		writeJSON(w, http.StatusBadRequest, map[string]string{
			"error": "a tag uses lowercase letters, digits, - and _"})
		return
	}
	// Which of the caller's links carry the old tag. Counted before the write,
	// because the write is "insert the new pair, drop the old one" and
	// RowsAffected on either half is not the number of links that moved.
	rows, err := s.DB.Query(`SELECT t.slug FROM short_link_tags t
		JOIN short_links l ON l.slug = t.slug
		WHERE t.tag = ? AND l.pwd_ref = ?`, old, ref)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "could not rename tag"})
		return
	}
	var slugs []string
	for rows.Next() {
		var sl string
		if rows.Scan(&sl) == nil {
			slugs = append(slugs, sl)
		}
	}
	rows.Close()

	tx, err := s.DB.Begin()
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "could not rename tag"})
		return
	}
	now := time.Now().UTC().Format(time.RFC3339)
	for _, sl := range slugs {
		// INSERT OR IGNORE, not UPDATE: a link may already carry the new tag
		// (it is a set), and an UPDATE would hit the primary key and abort the
		// rename for everyone else.
		if newT != "" {
			if _, err := tx.Exec(`INSERT OR IGNORE INTO short_link_tags (slug, tag, created_at)
				VALUES (?, ?, ?)`, sl, newT, now); err != nil {
				tx.Rollback()
				writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "could not rename tag"})
				return
			}
		}
		if _, err := tx.Exec(`DELETE FROM short_link_tags WHERE slug = ? AND tag = ?`, sl, old); err != nil {
			tx.Rollback()
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "could not rename tag"})
			return
		}
	}
	if err := tx.Commit(); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "could not rename tag"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]interface{}{
		"tag": newT, "renamed": len(slugs), "deleted": newT == ""})
}

// HandleAPIShortLinkTags — GET /api/shortlink-tags → {tags:[{tag,links,live}]}.
//
// The vocabulary, and the point of a tag: to be the SAME word as last time. A
// fresh spelling per link is a group of one, so autocomplete is not a
// convenience here, it is the feature. Counts ride along because a chooser that
// cannot tell "report (12 links)" from a typo made once is a chooser that
// spreads the typo.
func (s *Server) HandleAPIShortLinkTags(w http.ResponseWriter, r *http.Request) {
	ref := shortCallerRef(r)
	type tagInfo struct {
		Tag   string `json:"tag"`
		Links int    `json:"links"`
		Live  int    `json:"live"` // live guest keys carrying it — the renewable ones
		Last  string `json:"last_used,omitempty"`
	}
	infos := []tagInfo{}
	names := []string{}
	if ref == "" {
		writeJSON(w, http.StatusOK, map[string]interface{}{"tags": names, "detail": infos})
		return
	}
	now := time.Now().UTC().Format(time.RFC3339)
	rows, err := s.DB.Query(`SELECT t.tag, COUNT(*) AS n,
			SUM(CASE WHEN l.guest = 1 AND (l.revoked_at IS NULL OR l.revoked_at = '')
			         AND COALESCE(l.expires_at,'') > ? THEN 1 ELSE 0 END) AS live,
			MAX(COALESCE(t.created_at, l.created_at)) AS m
		FROM short_link_tags t JOIN short_links l ON l.slug = t.slug
		WHERE l.pwd_ref = ?
		GROUP BY t.tag ORDER BY m DESC LIMIT 50`, now, ref)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "database error"})
		return
	}
	defer rows.Close()
	for rows.Next() {
		var ti tagInfo
		if rows.Scan(&ti.Tag, &ti.Links, &ti.Live, &ti.Last) == nil {
			infos = append(infos, ti)
			names = append(names, ti.Tag)
		}
	}
	// `tags` is names only, most recently used first — the shape the datalist
	// wants and the one the old endpoint promised.
	writeJSON(w, http.StatusOK, map[string]interface{}{"tags": names, "detail": infos})
}

// shortSlugsWithTag lists the caller's live guest keys carrying one tag — what
// "renew #report" acts on.
func (s *Server) shortSlugsWithTag(tag, ref string) []string {
	rows, err := s.DB.Query(`SELECT t.slug FROM short_link_tags t
		JOIN short_links l ON l.slug = t.slug
		WHERE t.tag = ? AND l.pwd_ref = ? AND l.guest = 1
		  AND (l.revoked_at IS NULL OR l.revoked_at = '')`, tag, ref)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var sl string
		if rows.Scan(&sl) == nil {
			out = append(out, sl)
		}
	}
	sort.Strings(out)
	return out
}
