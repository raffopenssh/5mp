package srv

// Link previews (Open Graph / Twitter cards) and the sitemap.
//
// The About modal ("How this map works") is deep-linkable per section
// (`/?methods=fire`, srv/static/sectionlink.js). A chat client unfurling such
// a link fetches the URL WITHOUT a cookie, so what it sees is the password
// page — and until 2026-09-19 that page (and the short-link redirect in front
// of it) carried one generic title and image whatever the link pointed at.
//
// The sections are DERIVED from the template's `.methods-h` headings, not
// typed here: a heading added to globe.html appears in the sitemap and in the
// previews on the next build with no second list to keep in step.
//
// SAFETY. Only the methods text is preview-worthy: it is the same for every
// reader. A preview never renders anything from the shared VIEW (park, dates,
// patrol) — an OG image is fetched unauthenticated and cached by third
// parties, so a screenshot of a gated or guest-scoped map would publish it.

import (
	"html"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"
)

const (
	ogSite       = "https://five-megapixel-conservation.exe.xyz"
	ogDefTitle   = "5MP.globe - African Conservation Monitoring"
	ogDefDesc    = "Real-time fire detection, deforestation monitoring, and patrol tracking for 162 African keystone protected areas."
	ogDefImage   = ogSite + "/static/og-image.png"
	ogDefAlt     = "5MP.globe - conservation monitoring globe with live fire alerts, forest change and patrol tracking"
	ogMethodsImg = ogSite + "/static/og-methods.png"
)

type ogMeta struct {
	Title, Desc, Image, Alt, URL string
}

// methodsSection is one `.methods-h` heading of the About modal.
type methodsSection struct {
	Slug, Title, Desc string
}

var (
	methodsOnce   sync.Once
	methodsList   []methodsSection
	methodsBySlug map[string]methodsSection
	methodsMod    time.Time

	reMethodsH = regexp.MustCompile(`(?s)<h([34])\s+id="methods-([a-z0-9-]+)"\s+class="methods-h"[^>]*>(.*?)</h[34]>`)
	reTag      = regexp.MustCompile(`(?s)<[^>]+>`)
	reSpace    = regexp.MustCompile(`\s+`)
)

func stripTags(s string) string {
	return strings.TrimSpace(reSpace.ReplaceAllString(html.UnescapeString(reTag.ReplaceAllString(s, " ")), " "))
}

// loadMethodsSections parses the About modal headings once per process.
func (s *Server) loadMethodsSections() {
	methodsOnce.Do(func() {
		methodsBySlug = map[string]methodsSection{}
		path := filepath.Join(s.TemplatesDir, "globe.html")
		b, err := os.ReadFile(path)
		if err != nil {
			return
		}
		if fi, err := os.Stat(path); err == nil {
			methodsMod = fi.ModTime()
		}
		src := string(b)
		ms := reMethodsH.FindAllStringSubmatchIndex(src, -1)
		for i, m := range ms {
			slug := src[m[4]:m[5]]
			title := stripTags(src[m[6]:m[7]])
			end := len(src)
			if i+1 < len(ms) {
				end = ms[i+1][0]
			}
			// The section's own text: from after the heading to the next
			// heading (or a reasonable cap), first ~200 chars.
			body := src[m[1]:end]
			if cut := strings.Index(body, "<footer"); cut >= 0 {
				body = body[:cut]
			}
			desc := stripTags(body)
			if desc == "" || strings.HasPrefix(desc, "Loading") {
				desc = ogDefDesc
			}
			if r := []rune(desc); len(r) > 200 {
				desc = strings.TrimRight(string(r[:197]), " ,;:-–—") + "…"
			}
			sec := methodsSection{Slug: slug, Title: title, Desc: desc}
			methodsList = append(methodsList, sec)
			methodsBySlug[slug] = sec
		}
	})
}

// ogForURL describes the preview for a page URL (path + query). Only a
// `methods=` naming a real section changes anything.
func (s *Server) ogForURL(u *url.URL) ogMeta {
	s.loadMethodsSections()
	m := ogMeta{Title: ogDefTitle, Desc: ogDefDesc, Image: ogDefImage, Alt: ogDefAlt, URL: ogSite + "/"}
	if u == nil {
		return m
	}
	if sec, ok := methodsBySlug[u.Query().Get("methods")]; ok {
		m.Title = sec.Title + " · How this map works · 5MP.globe"
		m.Desc = sec.Desc
		m.Image = ogMethodsImg
		m.Alt = "How this map works — " + sec.Title
		m.URL = ogSite + "/?methods=" + url.QueryEscape(sec.Slug)
	}
	return m
}

// ogTags renders <title> + canonical + OG + Twitter tags for m.
func ogTags(m ogMeta) string {
	e := html.EscapeString
	return `<title>` + e(m.Title) + `</title>
    <meta name="description" content="` + e(m.Desc) + `">
    <link rel="canonical" href="` + e(m.URL) + `">
    <meta property="og:type" content="website">
    <meta property="og:title" content="` + e(m.Title) + `">
    <meta property="og:description" content="` + e(m.Desc) + `">
    <meta property="og:image" content="` + e(m.Image) + `">
    <meta property="og:image:width" content="1200">
    <meta property="og:image:height" content="630">
    <meta property="og:image:alt" content="` + e(m.Alt) + `">
    <meta property="og:url" content="` + e(m.URL) + `">
    <meta property="og:site_name" content="5MP.globe">
    <meta property="og:locale" content="en_US">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="` + e(m.Title) + `">
    <meta name="twitter:description" content="` + e(m.Desc) + `">
    <meta name="twitter:image" content="` + e(m.Image) + `">`
}

// isLinkPreviewBot: the unfurlers of chat and social clients. They follow a
// 302 to the password page and would show its generic card; served an OG
// document directly they show the link's own. Search engines are NOT here on
// purpose — they should follow the redirect like a person.
var reLinkPreviewBot = regexp.MustCompile(`(?i)facebookexternalhit|facebot|twitterbot|slackbot|linkedinbot|whatsapp|discordbot|telegrambot|skypeuripreview|mastodon|pinterest|redditbot|embedly|iframely|signal-desktop|matrix|element`)

func isLinkPreviewBot(r *http.Request) bool {
	return reLinkPreviewBot.MatchString(r.UserAgent())
}

// ogPreviewPage: a minimal document carrying only the preview tags and a link
// on to the real target. Used for short links when the client is an unfurler.
func ogPreviewPage(w http.ResponseWriter, m ogMeta, target string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "private, no-store")
	w.Write([]byte(`<!doctype html><html lang="en"><head><meta charset="utf-8">
    ` + ogTags(m) + `
    <meta name="robots" content="noindex">
    </head><body style="font:15px/1.6 -apple-system,system-ui,sans-serif;background:#0a0a0a;color:#e0e0e0;padding:14vh 8vw">
    <h1 style="font-size:20px;color:#fff">` + html.EscapeString(m.Title) + `</h1>
    <p style="color:#888">` + html.EscapeString(m.Desc) + `</p>
    <p><a style="color:#4ade80" href="` + html.EscapeString(target) + `">Open</a></p></body></html>`))
}

// HandleSitemap renders sitemap.xml: the static pages plus one URL per About
// section, derived from the template so the list cannot go stale.
func (s *Server) HandleSitemap(w http.ResponseWriter, r *http.Request) {
	s.loadMethodsSections()
	mod := methodsMod
	if mod.IsZero() {
		mod = time.Now()
	}
	lm := mod.UTC().Format("2006-01-02")
	var b strings.Builder
	b.WriteString(`<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:xhtml="http://www.w3.org/1999/xhtml">
  <url><loc>` + ogSite + `/</loc><lastmod>` + lm + `</lastmod><changefreq>daily</changefreq><priority>1.0</priority></url>
`)
	for _, sec := range methodsList {
		b.WriteString(`  <url><loc>` + ogSite + `/?methods=` + url.QueryEscape(sec.Slug) + `</loc><lastmod>` + lm + `</lastmod><changefreq>monthly</changefreq><priority>0.6</priority></url>
`)
	}
	for _, p := range []string{"impressum", "datenschutz"} {
		b.WriteString(`  <url><loc>` + ogSite + `/` + p + `</loc><lastmod>` + lm + `</lastmod><changefreq>yearly</changefreq><priority>0.2</priority>
    <xhtml:link rel="alternate" hreflang="de" href="` + ogSite + `/` + p + `"/>
    <xhtml:link rel="alternate" hreflang="en" href="` + ogSite + `/` + p + `?lang=en"/>
  </url>
`)
	}
	b.WriteString(`  <url><loc>` + ogSite + `/licenses</loc><lastmod>` + lm + `</lastmod><changefreq>monthly</changefreq><priority>0.3</priority></url>
</urlset>
`)
	w.Header().Set("Content-Type", "application/xml; charset=utf-8")
	w.Header().Set("Cache-Control", "public, max-age=3600")
	w.Write([]byte(b.String()))
}
