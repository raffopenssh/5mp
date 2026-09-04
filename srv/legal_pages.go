package srv

import (
	"net/http"
	"strings"
)

// Legal pages (Impressum & Datenschutzerklärung) per Austrian § 25 MedienG,
// § 5 ECG and GDPR, served in German (authoritative) and English
// (?lang=en). Served WITHOUT password protection (see PasswordMiddleware)
// so they are reachable from the login page.
//
// Keep these truthful: the app deliberately collects no analytics, no trackers,
// and persists no IP addresses (rate limiter is in-memory only). Personal data
// exists only for voluntarily registered alpha accounts and GPX uploads.

const legalStyle = `<style>
:root { color-scheme: dark; }
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  background:#0a0a0a; color:#c0c0c0; line-height:1.65; padding:40px 20px; }
main { max-width:720px; margin:0 auto; }
.legal-nav { margin-bottom:28px; display:flex; justify-content:space-between; align-items:center; }
.legal-nav a { color:#22c55e; text-decoration:none; font-size:14px; }
.legal-nav a:hover { text-decoration:underline; }
.lang-switch { font-size:13px; color:#555; }
.lang-switch a { color:#4ade80; }
.lang-switch .active { color:#888; }
h1 { color:#fff; font-size:26px; margin-bottom:8px; }
.lang-note { font-size:12px; color:#666; margin-bottom:28px; font-style:italic; }
h2 { color:#22c55e; font-size:16px; margin:28px 0 10px; }
h3 { color:#ddd; font-size:14px; margin:18px 0 8px; }
p { font-size:14px; margin-bottom:12px; }
ul { margin:0 0 12px 22px; font-size:14px; }
li { margin-bottom:4px; }
a { color:#4ade80; }
strong { color:#e0e0e0; }
.footer-links { margin-top:40px; padding-top:20px; border-top:1px solid rgba(255,255,255,0.08);
  font-size:13px; color:#555; }
.stand { margin-top:24px; font-size:12px; color:#555; }
</style>
<script>
// E-mail is XOR-encoded and only decoded on user interaction (click),
// so it never appears in the page source or the initial DOM. This keeps
// it out of reach of scrapers (including JS-executing ones) while staying
// one click away for humans.
(function(){document.addEventListener('DOMContentLoaded',function(){
 var k=73,a=[59,40,47,47,40,44,37,33,32,42,34,32,58,42,33,98,124,36,57],
     b=[46,36,40,32,37],c=[42,38,36];
 function dec(x){var s='';for(var i=0;i<x.length;i++)s+=String.fromCharCode(x[i]^k);return s;}
 document.querySelectorAll('.obf-email').forEach(function(el){
  var lang=document.documentElement.lang||'de';
  el.textContent=(lang==='en')?'[click to reveal]':'[klicken zum Anzeigen]';
  el.setAttribute('href','#');
  el.addEventListener('click',function(ev){
   ev.preventDefault();
   var e=dec(a)+String.fromCharCode(64)+dec(b)+String.fromCharCode(46)+dec(c);
   el.textContent=e;
   el.setAttribute('href','mai'+'lto:'+e);
  },{once:true});
 });});})();
</script>`

// legalLang picks the page language: explicit ?lang= wins, otherwise the
// browser's Accept-Language decides (German browsers get German, everyone
// else English). The German text remains the legally authoritative version.
func legalLang(r *http.Request) string {
	switch r.URL.Query().Get("lang") {
	case "en":
		return "en"
	case "de":
		return "de"
	}
	// Parse Accept-Language: first language tag that is de* or en* wins.
	for _, part := range strings.Split(r.Header.Get("Accept-Language"), ",") {
		lang := strings.ToLower(strings.TrimSpace(strings.SplitN(part, ";", 2)[0]))
		if strings.HasPrefix(lang, "de") {
			return "de"
		}
		if strings.HasPrefix(lang, "en") {
			return "en"
		}
	}
	return "en"
}

func legalNav(lang, path, backLabel string) string {
	deCls, enCls := " class=\"active\"", ""
	deHref, enHref := path+"?lang=de", path+"?lang=en"
	if lang == "en" {
		deCls, enCls = "", " class=\"active\""
	}
	return `<nav class="legal-nav">
<a href="/">&larr; ` + backLabel + `</a>
<span class="lang-switch"><a href="` + deHref + `"` + deCls + `>DE</a> &middot; <a href="` + enHref + `"` + enCls + `>EN</a></span>
</nav>`
}

func writeLegalPage(w http.ResponseWriter, lang, title, body string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Vary", "Accept-Language")
	w.Write([]byte(`<!DOCTYPE html>
<html lang="` + lang + `">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>` + title + ` | 5MP Conservation Tracker</title>
<meta name="robots" content="noindex">
` + legalStyle + `
</head>
<body>
<main>
` + body + `
</main>
</body>
</html>`))
}

// HandleImpressum serves /impressum (German default, ?lang=en for English).
func (s *Server) HandleImpressum(w http.ResponseWriter, r *http.Request) {
	if legalLang(r) == "en" {
		writeLegalPage(w, "en", "Legal Notice (Impressum)", legalNav("en", "/impressum", "Back")+`
<h1>Legal Notice (Impressum)</h1>
<p class="lang-note">This is a courtesy translation. The German version is legally authoritative.</p>

<h2>Information pursuant to &sect; 25 Austrian Media Act (MedienG) and &sect; 5 E-Commerce Act (ECG)</h2>
<p><strong>Media owner and publisher:</strong></p>
<p>Raffael Hickisch<br>Goldschlagstra&szlig;e 23/18<br>1150 Vienna<br>Austria</p>
<p><strong>Contact:</strong></p>
<p>E-mail: <a href="#" class="obf-email"></a></p>

<h2>Editorial purpose</h2>
<p>&ldquo;5MP Conservation Tracker&rdquo; (5 Megapixels of Global Conservation) is a
non-commercial project in its alpha stage for visualising conservation
monitoring data (fire detections, deforestation, settlement pressure, patrol
coverage) for African protected areas. The application serves to inform the
public, to support protected-area management, and to demonstrate data
processing methods.</p>

<h2>Disclaimer</h2>
<p>The contents of this website have been created with the greatest possible
care. The application is at an early development stage (alpha). No guarantee
can be given for the correctness, completeness or currency of the contents
&mdash; in particular the analyses derived automatically from satellite data.
Use of the contents is at your own risk; the contents are not suitable as the
sole basis for operational decisions in protected-area management.</p>
<p>We accept no liability for the contents of external links. The operators of
the linked pages are solely responsible for their content.</p>

<h2>Data sources and copyright</h2>
<p>The application uses the following third-party data, imagery and map
sheets. The respective licence and citation terms of the providers remain
unaffected; the full register with attribution lines, citations and the
conditions our use rests on is at <a href="/licenses">/licenses</a>.</p>
`+licenseSummaryList()+`
<p>The source code is released under the MIT licence; our own derived layers
under CC BY 4.0 unless an input imposes stricter terms.</p>
<p>Contents and works created by the site operator on these pages are subject
to Austrian copyright law.</p>

<div class="footer-links">
<a href="/">Home</a> &middot; <a href="/datenschutz?lang=en">Privacy Policy</a>
</div>`)
		return
	}
	writeLegalPage(w, "de", "Impressum", legalNav("de", "/impressum", "Zur&uuml;ck")+`
<h1>Impressum</h1>

<h2>Angaben gem&auml;&szlig; &sect; 25 MedienG und &sect; 5 ECG</h2>
<p><strong>Medieninhaber und Herausgeber:</strong></p>
<p>Raffael Hickisch<br>Goldschlagstra&szlig;e 23/18<br>1150 Wien<br>&Ouml;sterreich</p>
<p><strong>Kontakt:</strong></p>
<p>E-Mail: <a href="#" class="obf-email"></a></p>

<h2>Grundlegende Richtung</h2>
<p>&bdquo;5MP Conservation Tracker&ldquo; (5 Megapixels of Global Conservation) ist ein
nicht-kommerzielles Projekt in der Alpha-Phase zur Visualisierung von
Naturschutz-Monitoring-Daten (Feuer-Detektionen, Entwaldung, Siedlungsdruck,
Patrouillen-Abdeckung) f&uuml;r afrikanische Schutzgebiete. Die Anwendung dient der
Information der &Ouml;ffentlichkeit, der Unterst&uuml;tzung von Schutzgebietsmanagement
und der Demonstration von Datenverarbeitungsmethoden.</p>

<h2>Haftungsausschluss</h2>
<p>Die Inhalte dieser Website wurden mit gr&ouml;&szlig;tm&ouml;glicher Sorgfalt erstellt. Die
Anwendung befindet sich in einer fr&uuml;hen Entwicklungsphase (Alpha). F&uuml;r die
Richtigkeit, Vollst&auml;ndigkeit und Aktualit&auml;t der Inhalte &ndash; insbesondere der
automatisiert aus Satellitendaten abgeleiteten Analysen &ndash; kann keine Gew&auml;hr
&uuml;bernommen werden. Die Nutzung der Inhalte erfolgt auf eigene Gefahr; die
Inhalte sind nicht als alleinige Grundlage f&uuml;r operative Entscheidungen im
Schutzgebietsmanagement geeignet.</p>
<p>F&uuml;r die Inhalte externer Links &uuml;bernehmen wir keine Haftung. F&uuml;r den Inhalt
der verlinkten Seiten sind ausschlie&szlig;lich deren Betreiber verantwortlich.</p>

<h2>Datenquellen und Urheberrecht</h2>
<p>Die Anwendung nutzt folgende Daten, Bilddaten und Kartenwerke Dritter. Die
jeweiligen Lizenz- und Zitationsbedingungen der Anbieter bleiben unber&uuml;hrt;
das vollst&auml;ndige Verzeichnis mit Quellenangaben, Zitationen und den
Bedingungen, auf denen unsere Nutzung beruht, findet sich unter
<a href="/licenses">/licenses</a> (englisch).</p>
`+licenseSummaryList()+`
<p>Der Quellcode steht unter der MIT-Lizenz; unsere eigenen abgeleiteten
Ebenen unter CC BY 4.0, sofern eine Eingangsquelle keine strengeren
Bedingungen vorgibt.</p>
<p>Die durch den Seitenbetreiber erstellten Inhalte und Werke auf diesen Seiten
unterliegen dem &ouml;sterreichischen Urheberrecht.</p>

<div class="footer-links">
<a href="/">Startseite</a> &middot; <a href="/datenschutz">Datenschutz</a>
</div>`)
}

// HandleDatenschutz serves /datenschutz (German default, ?lang=en for English).
func (s *Server) HandleDatenschutz(w http.ResponseWriter, r *http.Request) {
	if legalLang(r) == "en" {
		writeLegalPage(w, "en", "Privacy Policy", legalNav("en", "/datenschutz", "Back")+`
<h1>Privacy Policy</h1>
<p class="lang-note">This is a courtesy translation. The German version is legally authoritative.</p>

<h2>1. Controller</h2>
<p>Raffael Hickisch<br>Goldschlagstra&szlig;e 23/18<br>1150 Vienna<br>Austria<br>
E-mail: <a href="#" class="obf-email"></a></p>

<h2>2. Principle: data minimisation</h2>
<p>This application is in its alpha stage and is deliberately built so that
<strong>no analytics or tracking data about visitors is collected</strong>.
There are no web analytics, no tracking cookies, no fingerprinting, and no
sharing of usage data with third parties. The application does not store IP
addresses.</p>

<h2>3. Data processed</h2>

<h3>3.1 Technically necessary cookies</h3>
<p>The application only sets functional cookies:</p>
<ul>
<li><strong>access_pwd</strong> &ndash; stores the alpha access password so you
do not have to re-enter it on every visit (validity: 30 days). This cookie
contains no personal data and no identifier that could identify you
individually.</li>
<li><strong>session</strong> &ndash; only if you voluntarily register a user
account: a session identifier for login.</li>
</ul>
<p>Legal basis: Art. 6(1)(b) and (f) GDPR (provision of the service,
legitimate interest in access protection during the alpha phase). No tracking
cookies are used.</p>

<h3>3.2 Local settings in your browser (localStorage)</h3>
<p>Display preferences (e.g. map settings, dismissed hints, a local page-view
counter) are stored exclusively in your browser's localStorage and are
<strong>never transmitted to the server</strong>. You can delete this data at
any time via your browser settings.</p>

<h3>3.3 User accounts (voluntary)</h3>
<p>If you voluntarily register an account, we store the data you provide:
e-mail address, name, organisation and organisation type, as well as a
password (stored only as a cryptographic hash). This data is used exclusively
to grant and manage your access. Legal basis: Art. 6(1)(b) GDPR.</p>

<h3>3.4 GPX uploads (voluntary)</h3>
<p>If you upload GPS tracks (GPX files) from patrols, we process the position
data they contain in order to compute aggregated patrol coverage
(10&times;10&nbsp;km grid cells). We store the file name, classified track
segments and &mdash; for logged-in users &mdash; the association with the
account. The original GPX files are not retained permanently after
processing. Please do not upload tracks that could reveal information about
vulnerable individuals without their consent. Legal basis: Art. 6(1)(a) and
(b) GDPR.</p>

<h3>3.5 Server log files and rate limiting</h3>
<p>The application itself does not write access logs containing personal
data. To protect against abuse, IP addresses are processed briefly
<strong>in memory only</strong> for rate limiting and are not stored. The
hosting provider (exe.dev) may keep technical log files at the infrastructure
level according to its own policies; see section 8. Legal basis:
Art. 6(1)(f) GDPR (operational security).</p>

<h2>4. No analytics or tracking services</h2>
<p>This website uses no analytics tools (such as Google Analytics, Matomo,
Plausible, etc.), no advertising networks, no social media plugins, and no
third-party tracking services.</p>

<h2>5. Embedded third parties (CDN and map services)</h2>
<p>To display the interactive map, resources are loaded from third-party
providers. For technical reasons, your IP address is transmitted to the
respective provider:</p>
<ul>
<li><strong>unpkg.com</strong> &ndash; MapLibre map library, icon font</li>
<li><strong>cdn.jsdelivr.net</strong> &ndash; export libraries</li>
<li><strong>demotiles.maplibre.org</strong> &ndash; map fonts</li>
<li><strong>Google Maps / Esri ArcGIS Online</strong> &ndash; satellite imagery
tiles, only if you enable the satellite view</li>
</ul>
<p>The dark base-map tiles (data &copy; OpenStreetMap contributors, rendering
&copy; CARTO) are <em>not</em> loaded from CARTO by your browser: this server
fetches them and passes them on, so your IP address is not transmitted to
CARTO.</p>
<p>In the context of this application, these services do not set tracking
cookies. Legal basis: Art. 6(1)(f) GDPR (legitimate interest in a functioning
map display).</p>

<h2>6. Retention period</h2>
<p>Account and upload data are stored for as long as the account exists or
until you request deletion. Since the application is in its alpha phase, data
sets may also be reset in the course of development. Session cookies expire
automatically.</p>

<h2>7. Your rights</h2>
<p>You have the right to:</p>
<ul>
<li>Access to your stored data (Art. 15 GDPR)</li>
<li>Rectification of inaccurate data (Art. 16 GDPR)</li>
<li>Erasure of your data (Art. 17 GDPR)</li>
<li>Restriction of processing (Art. 18 GDPR)</li>
<li>Data portability (Art. 20 GDPR)</li>
<li>Objection to processing (Art. 21 GDPR)</li>
<li>Withdrawal of consent given (Art. 7(3) GDPR)</li>
</ul>
<p>To exercise these rights, please contact us at the e-mail address given
above.</p>

<h2>8. Right to lodge a complaint</h2>
<p>You have the right to lodge a complaint with the Austrian data protection
authority:</p>
<p>&Ouml;sterreichische Datenschutzbeh&ouml;rde<br>Barichgasse 40-42<br>1030 Vienna<br>
<a href="https://www.dsb.gv.at" rel="noopener">www.dsb.gv.at</a></p>

<h2>9. Hosting</h2>
<p>This website is hosted by exe.dev (Bold Software, Inc., USA). The servers
are located in the USA. Data transfer to the USA takes place on the basis of
Art. 49(1)(b) GDPR (performance of the usage contract). More information:
<a href="https://exe.dev/docs/privacy-notice.md" rel="noopener">exe.dev
Privacy Notice</a>.</p>

<p class="stand">Last updated: July 2026</p>

<div class="footer-links">
<a href="/">Home</a> &middot; <a href="/impressum?lang=en">Legal Notice</a>
</div>`)
		return
	}
	writeLegalPage(w, "de", "Datenschutzerkl&auml;rung", legalNav("de", "/datenschutz", "Zur&uuml;ck")+`
<h1>Datenschutzerkl&auml;rung</h1>

<h2>1. Verantwortlicher</h2>
<p>Raffael Hickisch<br>Goldschlagstra&szlig;e 23/18<br>1150 Wien<br>&Ouml;sterreich<br>
E-Mail: <a href="#" class="obf-email"></a></p>

<h2>2. Grundsatz: Datenminimierung</h2>
<p>Diese Anwendung befindet sich in der Alpha-Phase und ist bewusst so gebaut,
dass <strong>keine Analyse- oder Tracking-Daten &uuml;ber Besucher erhoben
werden</strong>. Es gibt keine Web-Analytics, keine Tracking-Cookies, kein
Fingerprinting und keine Weitergabe von Nutzungsdaten an Dritte. IP-Adressen
werden von der Anwendung nicht gespeichert.</p>

<h2>3. Verarbeitete Daten</h2>

<h3>3.1 Technisch notwendige Cookies</h3>
<p>Die Anwendung setzt ausschlie&szlig;lich funktionale Cookies:</p>
<ul>
<li><strong>access_pwd</strong> &ndash; speichert das Alpha-Zugangspasswort, damit
Sie es nicht bei jedem Aufruf erneut eingeben m&uuml;ssen (G&uuml;ltigkeit: 30 Tage).
Dieses Cookie enth&auml;lt keine personenbezogenen Daten und keine Kennung, die Sie
individuell identifiziert.</li>
<li><strong>session</strong> &ndash; nur bei freiwilliger Registrierung eines
Benutzerkontos: eine Sitzungskennung f&uuml;r den Login.</li>
</ul>
<p>Rechtsgrundlage: Art. 6 Abs. 1 lit. b und f DSGVO (Bereitstellung des
Dienstes, berechtigtes Interesse an Zugangsschutz w&auml;hrend der Alpha-Phase).
Tracking-Cookies werden nicht verwendet.</p>

<h3>3.2 Lokale Einstellungen im Browser (localStorage)</h3>
<p>Anzeigeeinstellungen (z.&nbsp;B. Karteneinstellungen, angezeigte Hinweise,
ein lokaler Seitenaufruf-Z&auml;hler) werden ausschlie&szlig;lich im localStorage Ihres
Browsers gespeichert und <strong>niemals an den Server &uuml;bertragen</strong>.
Sie k&ouml;nnen diese Daten jederzeit &uuml;ber die Browsereinstellungen l&ouml;schen.</p>

<h3>3.3 Benutzerkonten (freiwillig)</h3>
<p>Wenn Sie freiwillig ein Konto registrieren, speichern wir die von Ihnen
angegebenen Daten: E-Mail-Adresse, Name, Organisation und Organisationstyp
sowie ein Passwort (nur als kryptografischer Hash). Diese Daten dienen
ausschlie&szlig;lich der Freischaltung und Verwaltung Ihres Zugangs.
Rechtsgrundlage: Art. 6 Abs. 1 lit. b DSGVO.</p>

<h3>3.4 GPX-Uploads (freiwillig)</h3>
<p>Wenn Sie GPS-Tracks (GPX-Dateien) von Patrouillen hochladen, verarbeiten
wir die enthaltenen Positionsdaten, um aggregierte Patrouillen-Abdeckung
(10&times;10-km-Rasterzellen) zu berechnen. Gespeichert werden der Dateiname,
klassifizierte Streckensegmente und &ndash; bei angemeldeten Nutzern &ndash; die Zuordnung
zum Konto. Die Original-GPX-Dateien werden nach der Verarbeitung nicht
dauerhaft aufbewahrt. Bitte laden Sie keine Tracks hoch, die R&uuml;ckschl&uuml;sse auf
schutzbed&uuml;rftige Personen zulassen, ohne deren Einwilligung.
Rechtsgrundlage: Art. 6 Abs. 1 lit. a und b DSGVO.</p>

<h3>3.5 Server-Logfiles und Rate-Limiting</h3>
<p>Die Anwendung selbst schreibt keine personenbezogenen Zugriffsprotokolle.
Zum Schutz vor Missbrauch werden IP-Adressen kurzzeitig
<strong>ausschlie&szlig;lich im Arbeitsspeicher</strong> f&uuml;r ein Rate-Limiting
verarbeitet und nicht gespeichert. Der Hosting-Anbieter (exe.dev) kann auf
Infrastrukturebene technische Logfiles nach eigenen Richtlinien f&uuml;hren; siehe
Abschnitt 8. Rechtsgrundlage: Art. 6 Abs. 1 lit. f DSGVO (Betriebssicherheit).</p>

<h2>4. Keine Analyse- und Trackingdienste</h2>
<p>Diese Website verwendet keine Analyse-Tools (wie Google Analytics, Matomo,
Plausible o.&nbsp;&auml;.), keine Werbenetzwerke, keine Social-Media-Plugins und
keine Tracking-Dienste von Drittanbietern.</p>

<h2>5. Eingebundene Drittanbieter (CDN und Kartendienste)</h2>
<p>Zur Darstellung der interaktiven Karte werden Ressourcen von Drittanbietern
geladen. Dabei wird technisch bedingt Ihre IP-Adresse an den jeweiligen
Anbieter &uuml;bermittelt:</p>
<ul>
<li><strong>unpkg.com</strong> &ndash; MapLibre-Kartenbibliothek, Icon-Schriftart</li>
<li><strong>cdn.jsdelivr.net</strong> &ndash; Export-Bibliotheken</li>
<li><strong>demotiles.maplibre.org</strong> &ndash; Kartenschriften</li>
<li><strong>Google Maps / Esri ArcGIS Online</strong> &ndash; Satellitenbild-Kacheln,
nur wenn Sie die Satellitenansicht aktivieren</li>
</ul>
<p>Die dunklen Basiskarten-Kacheln (Daten &copy; OpenStreetMap-Mitwirkende,
Darstellung &copy; CARTO) werden <em>nicht</em> von Ihrem Browser bei CARTO
geladen: dieser Server ruft sie ab und liefert sie aus, Ihre IP-Adresse wird
daher nicht an CARTO &uuml;bermittelt.</p>
<p>Diese Dienste setzen im Kontext dieser Anwendung keine Tracking-Cookies.
Rechtsgrundlage: Art. 6 Abs. 1 lit. f DSGVO (berechtigtes Interesse an einer
funktionierenden Kartendarstellung).</p>

<h2>6. Speicherdauer</h2>
<p>Kontodaten und Upload-Daten werden gespeichert, solange das Konto besteht
bzw. bis Sie die L&ouml;schung verlangen. Da sich die Anwendung in der Alpha-Phase
befindet, k&ouml;nnen Datenbest&auml;nde auch im Zuge der Entwicklung zur&uuml;ckgesetzt
werden. Sitzungs-Cookies verfallen automatisch.</p>

<h2>7. Ihre Rechte</h2>
<p>Sie haben das Recht auf:</p>
<ul>
<li>Auskunft &uuml;ber Ihre gespeicherten Daten (Art. 15 DSGVO)</li>
<li>Berichtigung unrichtiger Daten (Art. 16 DSGVO)</li>
<li>L&ouml;schung Ihrer Daten (Art. 17 DSGVO)</li>
<li>Einschr&auml;nkung der Verarbeitung (Art. 18 DSGVO)</li>
<li>Daten&uuml;bertragbarkeit (Art. 20 DSGVO)</li>
<li>Widerspruch gegen die Verarbeitung (Art. 21 DSGVO)</li>
<li>Widerruf erteilter Einwilligungen (Art. 7 Abs. 3 DSGVO)</li>
</ul>
<p>Zur Aus&uuml;bung dieser Rechte kontaktieren Sie uns bitte unter der oben
angegebenen E-Mail-Adresse.</p>

<h2>8. Beschwerderecht</h2>
<p>Sie haben das Recht, sich bei der &ouml;sterreichischen Datenschutzbeh&ouml;rde zu
beschweren:</p>
<p>&Ouml;sterreichische Datenschutzbeh&ouml;rde<br>Barichgasse 40-42<br>1030 Wien<br>
<a href="https://www.dsb.gv.at" rel="noopener">www.dsb.gv.at</a></p>

<h2>9. Hosting</h2>
<p>Diese Website wird bei exe.dev (Bold Software, Inc., USA) gehostet. Die
Server befinden sich in den USA. Die Daten&uuml;bermittlung in die USA erfolgt auf
Grundlage von Art. 49 Abs. 1 lit. b DSGVO (Erf&uuml;llung des Nutzungsvertrags).
Weitere Informationen: <a href="https://exe.dev/docs/privacy-notice.md" rel="noopener">exe.dev
Privacy Notice</a>.</p>

<p class="stand">Stand: Juli 2026</p>

<div class="footer-links">
<a href="/">Startseite</a> &middot; <a href="/impressum">Impressum</a>
</div>`)
}

// legalFooterLabels returns the [impressum, datenschutz] link labels in the
// browser's language (used on the login page footer).
func legalFooterLabels(r *http.Request) [2]string {
	if legalLang(r) == "en" {
		return [2]string{"Legal Notice", "Privacy"}
	}
	return [2]string{"Impressum", "Datenschutz"}
}
