package srv

import (
	"crypto/subtle"
	"html"
	"net/http"
	"os"
	"strings"
)

// validPasswords are loaded from the ACCESS_PASSWORDS env var (comma-separated),
// falling back to the local secrets.env config file, then to a test-only default.
// Real credentials must never be committed to the repo (see secrets.env.example).
var validPasswords = loadPasswords()

func loadPasswords() []string {
	if env := os.Getenv("ACCESS_PASSWORDS"); env != "" {
		return strings.Split(env, ",")
	}
	if v := secretsEnv("ACCESS_PASSWORDS"); v != "" {
		return strings.Split(v, ",")
	}
	return []string{"test2026"}
}

// secretsEnv reads a KEY=VALUE entry from the local secrets.env file
// (gitignored; see secrets.env.example for the template).
func secretsEnv(key string) string {
	data, err := os.ReadFile("secrets.env")
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if k, v, ok := strings.Cut(line, "="); ok && strings.TrimSpace(k) == key {
			return strings.TrimSpace(v)
		}
	}
	return ""
}

// PasswordMiddleware checks for valid password in cookie or query param
func (s *Server) PasswordMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Allow static assets, downloads and SEO files without password
		if strings.HasSuffix(r.URL.Path, ".css") ||
			strings.HasSuffix(r.URL.Path, ".js") ||
			strings.HasSuffix(r.URL.Path, ".svg") ||
			strings.HasPrefix(r.URL.Path, "/static/downloads/") ||
			r.URL.Path == "/static/og-image.png" ||
			r.URL.Path == "/static/og-image.svg" ||
			r.URL.Path == "/healthz" ||
			r.URL.Path == "/impressum" ||
			r.URL.Path == "/datenschutz" ||
			r.URL.Path == "/robots.txt" ||
			r.URL.Path == "/sitemap.xml" ||
			r.URL.Path == "/static/robots.txt" ||
			r.URL.Path == "/static/sitemap.xml" {
			next.ServeHTTP(w, r)
			return
		}

		// Check cookie first
		cookie, err := r.Cookie("access_pwd")
		if err == nil && isValidPassword(cookie.Value) {
			next.ServeHTTP(w, r)
			return
		}

		// Check query param (for setting cookie)
		pwd := r.URL.Query().Get("pwd")
		if isValidPassword(pwd) {
			// Set cookie for future requests. This happens for API paths too:
			// a shared download link (/api/geopackage/{id}/download) is a page
			// someone opens in a browser, and after logging in through the form
			// they should be logged in -- not have to type the password again
			// the moment they click anything else.
			http.SetCookie(w, &http.Cookie{
				Name:     "access_pwd",
				Value:    pwd,
				Path:     "/",
				MaxAge:   86400 * 30, // 30 days
				HttpOnly: true,
				Secure:   true,
				SameSite: http.SameSiteLaxMode,
			})
			// For API endpoints, serve directly: an XHR follows a redirect
			// silently but a download must arrive as the response to *this*
			// request, and RequestEnv/RequestPwd read the param anyway.
			if strings.HasPrefix(r.URL.Path, "/api/") {
				next.ServeHTTP(w, r)
				return
			}
			// Redirect to remove pwd from URL
			cleanURL := r.URL.Path
			if r.URL.RawQuery != "" {
				q := r.URL.Query()
				q.Del("pwd")
				if encoded := q.Encode(); encoded != "" {
					cleanURL += "?" + encoded
				}
			}
			http.Redirect(w, r, cleanURL, http.StatusFound)
			return
		}

		// Show password form
		s.showPasswordForm(w, r)
	})
}

// RequestEnv returns "test" if the request authenticated with the test
// environment password (via pwd query param or access_pwd cookie), else "prod".
func RequestEnv(r *http.Request) string {
	if r == nil {
		return "prod"
	}
	if r.URL.Query().Get("pwd") == "test2026" {
		return "test"
	}
	if c, err := r.Cookie("access_pwd"); err == nil && c.Value == "test2026" {
		return "test"
	}
	return "prod"
}

// RequestPwd returns the access password used to authenticate this request
// (query param or cookie), or "" if none. Used only to label the alpha
// session chip in the UI; once real user management lands, the chip should
// prefer the authenticated user identity over this.
func RequestPwd(r *http.Request) string {
	if r == nil {
		return ""
	}
	if pwd := r.URL.Query().Get("pwd"); isValidPassword(pwd) {
		return pwd
	}
	if c, err := r.Cookie("access_pwd"); err == nil && isValidPassword(c.Value) {
		return c.Value
	}
	return ""
}

// ClearAccessPwdCookie removes the alpha access-password cookie.
func ClearAccessPwdCookie(w http.ResponseWriter) {
	http.SetCookie(w, &http.Cookie{
		Name:     "access_pwd",
		Value:    "",
		Path:     "/",
		MaxAge:   -1,
		HttpOnly: true,
		Secure:   true,
		SameSite: http.SameSiteLaxMode,
	})
}

func isValidPassword(pwd string) bool {
	for _, valid := range validPasswords {
		if subtle.ConstantTimeCompare([]byte(pwd), []byte(valid)) == 1 {
			return true
		}
	}
	return false
}

func (s *Server) showPasswordForm(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	// Pages: 200 (not 401) so social-media link scrapers (Facebook, Slack,
	// WhatsApp, Twitter) parse the Open Graph tags on this page; most refuse
	// non-2xx responses. API paths keep a proper 401 for clients.
	if strings.HasPrefix(r.URL.Path, "/api/") {
		w.WriteHeader(http.StatusUnauthorized)
	} else {
		w.WriteHeader(http.StatusOK)
	}

	// Build hidden fields for existing query params (preserve through login)
	var hiddenFields string
	for key, values := range r.URL.Query() {
		if key == "pwd" {
			continue
		}
		for _, val := range values {
			hiddenFields += `<input type="hidden" name="` + html.EscapeString(key) + `" value="` + html.EscapeString(val) + `">`
		}
	}

	// Tryout link must also carry the current params through login
	tryoutQuery := r.URL.Query()
	tryoutQuery.Set("pwd", "test2026")
	tryoutHref := r.URL.Path + "?" + tryoutQuery.Encode()

	// A shared file link is not the landing page. Someone who was sent
	// /api/geopackage/{id}/download opened it to get a file, so the form says
	// so -- and the sandbox link is dropped, because the demo password does not
	// own that export and would land them on a 404 that reads as a dead link.
	// The filename is deliberately NOT shown: it carries the area's name, and
	// an id must not be an oracle to someone who merely guessed the URL.
	isFileLink := strings.HasPrefix(r.URL.Path, "/api/") &&
		(strings.HasSuffix(r.URL.Path, "/download") || strings.Contains(r.URL.Path, "/export."))
	intro := `<p>Real-time fire detection, deforestation monitoring, and patrol tracking for 162 African keystone protected areas. Generate custom reports for managers, governments, and donors.</p>
        <div class="feature-chips">
            <span class="feature-chip"><span class="dot fire"></span>Live fire alerts</span>
            <span class="feature-chip"><span class="dot forest"></span>Forest change</span>
            <span class="feature-chip"><span class="dot patrol"></span>Patrol tracking</span>
        </div>`
	tryout := `<div style="margin-top:18px;display:flex;align-items:center;gap:10px;">
            <span style="flex:1;height:1px;background:rgba(255,255,255,0.08);"></span>
            <span style="color:#555;font-size:11px;letter-spacing:0.5px;">or</span>
            <span style="flex:1;height:1px;background:rgba(255,255,255,0.08);"></span>
        </div>
        <a href="` + html.EscapeString(tryoutHref) + `" style="display:block;margin-top:14px;padding:11px 16px;border:1px solid rgba(34,197,94,0.35);border-radius:10px;color:#4ade80;font-size:14px;font-weight:500;text-decoration:none;transition:all 0.2s;" onmouseover="this.style.background='rgba(34,197,94,0.1)';this.style.borderColor='rgba(34,197,94,0.6)'" onmouseout="this.style.background='transparent';this.style.borderColor='rgba(34,197,94,0.35)'">Just try it out — no password needed</a>
        <div style="margin-top:6px;font-size:11px;color:#555;">Sandbox with sample data. Nothing you do affects the live system.</div>`
	prompt := `Enter access password to continue`
	if isFileLink {
		intro = `<p>Someone shared a data export with you. Sign in with the access password you were given and the download starts right away.</p>`
		tryout = ""
		prompt = `Sign in to download`
	}

	html := `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, interactive-widget=resizes-content">
    <title>5MP.globe - African Conservation Monitoring</title>
    <meta name="description" content="Real-time fire detection, deforestation monitoring, and patrol tracking for 162 African keystone protected areas. Alpha access.">
    <meta name="robots" content="noindex">
    <link rel="canonical" href="https://five-megapixel-conservation.exe.xyz/">
    <meta property="og:type" content="website">
    <meta property="og:title" content="5MP.globe - African Conservation Monitoring">
    <meta property="og:description" content="Real-time fire detection, deforestation monitoring, and patrol tracking for 162 African keystone protected areas.">
    <meta property="og:image" content="https://five-megapixel-conservation.exe.xyz/static/og-image.png">
    <meta property="og:image:width" content="1200">
    <meta property="og:image:height" content="630">
    <meta property="og:image:alt" content="5MP.globe - conservation monitoring globe with live fire alerts, forest change and patrol tracking">
    <meta property="og:url" content="https://five-megapixel-conservation.exe.xyz/">
    <meta property="og:site_name" content="5MP.globe">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="5MP.globe - African Conservation Monitoring">
    <meta name="twitter:description" content="Real-time fire detection, deforestation monitoring, and patrol tracking for 162 African keystone protected areas.">
    <meta name="twitter:image" content="https://five-megapixel-conservation.exe.xyz/static/og-image.png">
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%2322c55e' stroke-width='2'%3E%3Ccircle cx='12' cy='12' r='10'/%3E%3Cellipse cx='12' cy='12' rx='4' ry='10'/%3E%3Cpath d='M2 12h20'/%3E%3C/svg%3E">
    <meta name="theme-color" content="#0a0a0a">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
            background: #0a0a0a; 
            color: #e0e0e0; 
            min-height: 100vh;
            min-height: 100dvh;
            display: flex; 
            align-items: center; 
            justify-content: center;
            position: relative;
            overflow-x: hidden;
            overflow-y: auto;
        }
        
        /* Animated background gradient */
        .bg-gradient {
            position: fixed;
            inset: 0;
            background: radial-gradient(ellipse at 50% 50%, rgba(34,197,94,0.08) 0%, transparent 50%),
                        radial-gradient(ellipse at 80% 20%, rgba(34,197,94,0.05) 0%, transparent 40%),
                        radial-gradient(ellipse at 20% 80%, rgba(22,163,74,0.05) 0%, transparent 40%);
            animation: gradientShift 15s ease-in-out infinite;
        }
        
        @keyframes gradientShift {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.7; transform: scale(1.1); }
        }
        
        /* Floating particles effect */
        .particles {
            position: fixed;
            inset: 0;
            overflow: hidden;
            pointer-events: none;
        }
        
        .particle {
            position: absolute;
            width: 2px;
            height: 2px;
            background: rgba(34,197,94,0.4);
            border-radius: 50%;
            animation: float 20s infinite;
        }
        
        .particle:nth-child(1) { left: 10%; animation-delay: 0s; animation-duration: 25s; }
        .particle:nth-child(2) { left: 20%; animation-delay: 2s; animation-duration: 20s; }
        .particle:nth-child(3) { left: 30%; animation-delay: 4s; animation-duration: 28s; }
        .particle:nth-child(4) { left: 40%; animation-delay: 1s; animation-duration: 22s; }
        .particle:nth-child(5) { left: 50%; animation-delay: 3s; animation-duration: 24s; }
        .particle:nth-child(6) { left: 60%; animation-delay: 5s; animation-duration: 26s; }
        .particle:nth-child(7) { left: 70%; animation-delay: 2s; animation-duration: 21s; }
        .particle:nth-child(8) { left: 80%; animation-delay: 4s; animation-duration: 23s; }
        .particle:nth-child(9) { left: 90%; animation-delay: 1s; animation-duration: 27s; }
        
        @keyframes float {
            0% { transform: translateY(100vh) scale(0); opacity: 0; }
            10% { opacity: 1; }
            90% { opacity: 1; }
            100% { transform: translateY(-100vh) scale(1); opacity: 0; }
        }
        
        .container { 
            background: rgba(18,18,18,0.95); 
            border: 1px solid rgba(255,255,255,0.1); 
            border-radius: 16px; 
            padding: 48px 40px; 
            width: 100%; 
            max-width: 380px; 
            margin: auto;
            text-align: center;
            backdrop-filter: blur(10px);
            box-shadow: 0 25px 50px -12px rgba(0,0,0,0.5),
                        0 0 0 1px rgba(255,255,255,0.05);
            position: relative;
            z-index: 10;
            animation: containerAppear 0.6s ease-out;
        }
        
        @keyframes containerAppear {
            0% { opacity: 0; transform: translateY(20px) scale(0.98); }
            100% { opacity: 1; transform: translateY(0) scale(1); }
        }
        
        .logo { 
            width: 72px;
            height: 72px;
            margin: 0 auto 16px auto; 
            display: flex;
            align-items: center;
            justify-content: center;
            position: relative;
        }
        
        .logo::before {
            content: '';
            position: absolute;
            inset: -10px;
            border-radius: 50%;
            background: radial-gradient(circle, rgba(34,197,94,0.18) 0%, transparent 70%);
            animation: haloPulse 4s ease-in-out infinite;
        }
        
        @keyframes haloPulse {
            0%, 100% { opacity: 0.6; transform: scale(1); }
            50% { opacity: 1; transform: scale(1.15); }
        }
        
        .logo svg {
            width: 56px;
            height: 56px;
            animation: globePulse 4s ease-in-out infinite;
        }
        
        @keyframes globePulse {
            0%, 100% { transform: scale(1) rotate(0deg); }
            25% { transform: scale(1.05) rotate(-2deg); }
            50% { transform: scale(1) rotate(0deg); }
            75% { transform: scale(1.05) rotate(2deg); }
        }
        
        /* Orbiting satellite dot around the globe */
        .logo .orbit {
            position: absolute;
            inset: -6px;
            border-radius: 50%;
            animation: orbitSpin 7s linear infinite;
            pointer-events: none;
        }
        .logo .orbit::after {
            content: '';
            position: absolute;
            top: 50%; left: -2px;
            width: 5px; height: 5px;
            border-radius: 50%;
            background: #4ade80;
            box-shadow: 0 0 8px 2px rgba(74,222,128,0.7);
        }
        @keyframes orbitSpin { to { transform: rotate(360deg); } }
        
        /* Feature chips */
        .feature-chips {
            display: flex;
            justify-content: center;
            gap: 8px;
            flex-wrap: wrap;
            margin: 0 0 22px;
        }
        .feature-chip {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-size: 11px;
            color: #9ca3af;
            border: 1px solid rgba(255,255,255,0.1);
            background: rgba(255,255,255,0.03);
            border-radius: 999px;
            padding: 5px 11px;
            opacity: 0;
            animation: chipIn 0.5s ease forwards;
        }
        .feature-chip:nth-child(1) { animation-delay: 0.25s; }
        .feature-chip:nth-child(2) { animation-delay: 0.4s; }
        .feature-chip:nth-child(3) { animation-delay: 0.55s; }
        .feature-chip .dot { width: 6px; height: 6px; border-radius: 50%; flex: none; }
        .feature-chip .dot.fire { background: #ef4444; box-shadow: 0 0 6px rgba(239,68,68,0.7); animation: dotBlink 2.2s ease-in-out infinite; }
        .feature-chip .dot.forest { background: #22c55e; box-shadow: 0 0 6px rgba(34,197,94,0.7); animation: dotBlink 2.2s ease-in-out 0.7s infinite; }
        .feature-chip .dot.patrol { background: #3b82f6; box-shadow: 0 0 6px rgba(59,130,246,0.7); animation: dotBlink 2.2s ease-in-out 1.4s infinite; }
        @keyframes dotBlink { 0%,100% { opacity: 1; } 50% { opacity: 0.35; } }
        @keyframes chipIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
        
        .alpha-line {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            margin-bottom: 16px;
            font-size: 12px;
            color: #666;
        }
        .alpha-badge {
            display: inline-block;
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 1px;
            text-transform: uppercase;
            color: #fbbf24;
            border: 1px solid rgba(251,191,36,0.35);
            background: rgba(251,191,36,0.08);
            border-radius: 6px;
            padding: 3px 8px;
        }
        
        .logo-text {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            margin-bottom: 8px;
        }
        
        h1 { 
            font-size: 26px; 
            font-weight: 600; 
            color: #fff;
            letter-spacing: -0.5px;
        }
        
        .subtitle {
            font-size: 11px;
            font-weight: 400;
            color: #22c55e;
            text-transform: uppercase;
            letter-spacing: 2px;
            margin-bottom: 24px;
        }
        
        p { 
            font-size: 14px; 
            color: #888; 
            margin-bottom: 18px;
            line-height: 1.5;
        }
        
        .form-group { margin-bottom: 16px; }
        
        input[type="password"] {
            width: 100%;
            padding: 14px 16px;
            background: rgba(10,10,10,0.8);
            border: 1px solid rgba(255,255,255,0.12);
            border-radius: 8px;
            color: #fff;
            font-size: 16px;
            text-align: center;
            letter-spacing: 3px;
            transition: all 0.3s ease;
        }
        
        input[type="password"]:focus {
            outline: none;
            border-color: #22c55e;
            background: rgba(10,10,10,0.95);
            box-shadow: 0 0 0 3px rgba(34,197,94,0.1);
        }
        
        input[type="password"]::placeholder { 
            color: #555; 
            letter-spacing: normal;
        }
        
        button {
            width: 100%;
            padding: 14px;
            background: linear-gradient(135deg, #22c55e, #16a34a);
            border: none;
            border-radius: 8px;
            color: #fff;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
        }
        
        button::before {
            content: '';
            position: absolute;
            inset: 0;
            background: linear-gradient(135deg, transparent, rgba(255,255,255,0.1), transparent);
            transform: translateX(-100%);
            transition: transform 0.5s ease;
        }
        
        button:hover { 
            background: linear-gradient(135deg, #16a34a, #15803d);
            transform: translateY(-1px);
            box-shadow: 0 4px 14px rgba(34,197,94,0.4);
        }
        
        button:hover::before {
            transform: translateX(100%);
        }
        
        button:active {
            transform: translateY(0);
        }
        
        .footer { 
            margin-top: 32px; 
            padding-top: 24px;
            border-top: 1px solid rgba(255,255,255,0.08);
            font-size: 12px; 
            color: #555;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }
        
        .footer-icon {
            color: #22c55e;
            font-size: 14px;
        }
        
        .footer a {
            color: #555;
            text-decoration: none;
            transition: color 0.2s ease;
        }
        
        .footer a:hover { color: #888; }
        
        .footer-sep { color: #333; }
        
        @media (max-width: 480px) {
            body { padding: 16px; }
            .container {
                padding: 36px 28px;
            }
            .logo { width: 48px; height: 48px; }
            .logo svg { width: 42px; height: 42px; }
            h1 { font-size: 22px; }
        }

        /* Compact layout when the on-screen keyboard shrinks the viewport */
        @media (max-height: 620px) {
            body { padding: 12px; }
            .container { padding: 20px 24px; }
            .logo, .logo .orbit { display: none; }
            .subtitle { margin-bottom: 10px; }
            p { display: none; }
            .feature-chips { display: none; }
            .alpha-line { margin-bottom: 10px; }
            .form-group { margin-bottom: 10px; }
            input[type="password"], button { padding: 11px 14px; }
            .footer { margin-top: 14px; padding-top: 12px; }
        }
    </style>
</head>
<body>
    <div class="bg-gradient"></div>
    <div class="particles">
        <div class="particle"></div>
        <div class="particle"></div>
        <div class="particle"></div>
        <div class="particle"></div>
        <div class="particle"></div>
        <div class="particle"></div>
        <div class="particle"></div>
        <div class="particle"></div>
        <div class="particle"></div>
    </div>
    <div class="container">
        <div class="logo">
            <div class="orbit"></div>
            <svg viewBox="0 0 24 24" fill="none" stroke="#22c55e" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="12" cy="12" r="10"></circle>
                <ellipse cx="12" cy="12" rx="4" ry="10"></ellipse>
                <path d="M2 12h20"></path>
                <path d="M4.5 6.5h15"></path>
                <path d="M4.5 17.5h15"></path>
            </svg>
        </div>
        <div class="logo-text">
            <h1>5MP.globe</h1>
        </div>
        <div class="subtitle">Conservation Tracker</div>
        ` + intro + `
        <div class="alpha-line"><span class="alpha-badge">Alpha</span><span>` + prompt + `</span></div>
        <form method="GET">
            ` + hiddenFields + `
            <div class="form-group">
                <input type="password" name="pwd" placeholder="Enter password" autofocus required>
            </div>
            <button type="submit">Continue →</button>
        </form>
        ` + tryout + `
        <div class="footer">
            <a href="/impressum">` + legalFooterLabels(r)[0] + `</a>
            <span class="footer-sep">&middot;</span>
            <a href="/datenschutz">` + legalFooterLabels(r)[1] + `</a>
        </div>
    </div>
    <script>
    (function(){
        var input = document.querySelector('input[name="pwd"]');
        var form = document.querySelector('form');
        if (!input || !form) return;
        // When the keyboard appears, make sure the form (input + button) stays visible
        input.addEventListener('focus', function(){
            setTimeout(function(){
                form.scrollIntoView({ block: 'center', behavior: 'smooth' });
            }, 300);
        });
        if (window.visualViewport) {
            window.visualViewport.addEventListener('resize', function(){
                if (document.activeElement === input) {
                    form.scrollIntoView({ block: 'center' });
                }
            });
        }
    })();
    </script>
</body>
</html>`
	w.Write([]byte(html))
}
