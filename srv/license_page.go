package srv

import (
	"html"
	"net/http"
	"strings"
)

// HandleLicensesPage renders the register as a page: /licenses. Public, like
// the Impressum it is linked from, and generated from the same list the API
// serves, so it cannot say something /api/licenses does not.
func (s *Server) HandleLicensesPage(w http.ResponseWriter, r *http.Request) {
	var b strings.Builder
	b.WriteString(`<nav class="legal-nav"><a href="/">&larr; Back</a></nav>`)
	b.WriteString(`<h1>Data sources &amp; licences</h1>
<p>Everything this map shows was measured, drawn or published by somebody
else first. This page lists every third-party work the application uses,
the terms its publisher granted, and the credit line they ask for.
A machine-readable copy is at <a href="/api/licenses">/api/licenses</a>.</p>

<h2>What we release</h2>
<ul>
<li><strong>Code:</strong> ` + html.EscapeString(ProjectLicence.Code) + ` &mdash; <a href="` + ProjectLicence.Repository + `">` + ProjectLicence.Repository + `</a></li>
<li><strong>Derived layers:</strong> ` + html.EscapeString(ProjectLicence.Derived) + `</li>
<li>` + html.EscapeString(ProjectLicence.NonCommercial) + `</li>
</ul>
<p class="lang-note">Terms in one word: <strong>open</strong> = a named open licence;
<strong>restricted</strong> = usable here under the stated condition;
<strong>unstated</strong> = the publisher granted no licence &mdash; we attribute
and cite, and you may not redistribute it further on our say-so.</p>`)

	cat := ""
	for _, e := range sortedLicenses() {
		if e.Category != cat {
			cat = e.Category
			b.WriteString("<h2>" + html.EscapeString(licenseCategoryTitle(cat)) + "</h2>")
		}
		b.WriteString(`<h3><a href="` + html.EscapeString(e.URL) + `">` + html.EscapeString(e.Name) + `</a> <span class="terms terms-` + string(e.Terms) + `">` + string(e.Terms) + `</span></h3><ul>`)
		b.WriteString("<li><strong>Publisher:</strong> " + html.EscapeString(e.Publisher) + "</li>")
		b.WriteString("<li><strong>Used for:</strong> " + html.EscapeString(e.Use) + "</li>")
		lic := html.EscapeString(e.Licence)
		if e.LicenceURL != "" {
			lic = `<a href="` + html.EscapeString(e.LicenceURL) + `">` + lic + `</a>`
		}
		b.WriteString("<li><strong>Licence:</strong> " + lic + "</li>")
		b.WriteString("<li><strong>Attribution:</strong> " + html.EscapeString(e.Attribution) + "</li>")
		if e.Citation != "" {
			b.WriteString("<li><strong>Cite:</strong> " + html.EscapeString(e.Citation) + "</li>")
		}
		if e.Notes != "" {
			b.WriteString("<li><strong>Note:</strong> " + html.EscapeString(e.Notes) + "</li>")
		}
		b.WriteString("</ul>")
	}
	b.WriteString(`<div class="footer-links"><a href="/">Home</a> &middot; <a href="/impressum?lang=en">Legal Notice</a> &middot; <a href="/datenschutz?lang=en">Privacy Policy</a></div>
<style>.terms{font-size:11px;font-weight:normal;padding:1px 7px;border-radius:9px;margin-left:6px;vertical-align:middle;color:#0a0a0a}
.terms-open{background:#22c55e}.terms-restricted{background:#f59e0b}.terms-unstated{background:#9ca3af}</style>`)
	writeLegalPage(w, "en", "Data sources & licences", b.String())
}

func licenseCategoryTitle(c string) string {
	switch c {
	case "imagery":
		return "Imagery, basemaps and map sheets"
	case "data":
		return "Datasets"
	}
	return "Software"
}

// licenseSummaryList is the one-line-per-source list the Impressum prints,
// derived from the register so the Impressum cannot name nine sources while
// the app uses thirty.
func licenseSummaryList() string {
	var b strings.Builder
	b.WriteString("<ul>")
	for _, e := range sortedLicenses() {
		if e.Category == "software" {
			continue
		}
		b.WriteString(`<li><a href="` + html.EscapeString(e.URL) + `">` + html.EscapeString(e.Name) + `</a> &mdash; ` + html.EscapeString(e.Publisher) + ` (` + html.EscapeString(e.Licence) + `)</li>`)
	}
	b.WriteString("</ul>")
	return b.String()
}
