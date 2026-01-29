package srv

import (
	"crypto/subtle"
	"net/http"
	"strings"
)

// Valid passwords for testing access
var validPasswords = []string{"ngi2026", "apn2026", "j2026"}

// PasswordMiddleware checks for valid password in cookie or query param
func (s *Server) PasswordMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Allow static downloads without password (or with password param)
		if r.URL.Path == "/static/downloads/5mp_data.sqlite3" {
			pwd := r.URL.Query().Get("pwd")
			if pwd == "" || isValidPassword(pwd) {
				next.ServeHTTP(w, r)
				return
			}
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
			// For API endpoints, just serve directly (no redirect)
			if strings.HasPrefix(r.URL.Path, "/api/") {
				next.ServeHTTP(w, r)
				return
			}
			// Set cookie for future requests
			http.SetCookie(w, &http.Cookie{
				Name:     "access_pwd",
				Value:    pwd,
				Path:     "/",
				MaxAge:   86400 * 30, // 30 days
				HttpOnly: true,
				SameSite: http.SameSiteLaxMode,
			})
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
	w.WriteHeader(http.StatusUnauthorized)
	html := `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Access Required - 5MP.globe</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
            background: #0a0a0a; 
            color: #e0e0e0; 
            min-height: 100vh; 
            display: flex; 
            align-items: center; 
            justify-content: center;
        }
        .container { 
            background: rgba(18,18,18,0.95); 
            border: 1px solid rgba(255,255,255,0.1); 
            border-radius: 16px; 
            padding: 40px; 
            width: 100%; 
            max-width: 360px; 
            text-align: center;
        }
        .logo { font-size: 48px; margin-bottom: 16px; }
        h1 { font-size: 24px; font-weight: 600; margin-bottom: 8px; color: #fff; }
        p { font-size: 14px; color: #888; margin-bottom: 24px; }
        .form-group { margin-bottom: 16px; }
        input[type="password"] {
            width: 100%;
            padding: 14px 16px;
            background: #0a0a0a;
            border: 1px solid rgba(255,255,255,0.15);
            border-radius: 8px;
            color: #fff;
            font-size: 16px;
            text-align: center;
            letter-spacing: 2px;
        }
        input[type="password"]:focus {
            outline: none;
            border-color: #22c55e;
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
            transition: all 0.2s;
        }
        button:hover { background: linear-gradient(135deg, #16a34a, #15803d); }
        .footer { margin-top: 24px; font-size: 12px; color: #555; }
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">🌍</div>
        <h1>5MP.globe</h1>
        <p>This is a testing version. Please enter the access password.</p>
        <form method="GET">
            <div class="form-group">
                <input type="password" name="pwd" placeholder="Enter password" autofocus required>
            </div>
            <button type="submit">Continue</button>
        </form>
        <div class="footer">Global Conservation Effort</div>
    </div>
</body>
</html>`
	w.Write([]byte(html))
}
