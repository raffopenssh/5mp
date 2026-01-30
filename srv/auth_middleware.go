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
            position: relative;
            overflow: hidden;
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
            width: 64px;
            height: 64px;
            margin: 0 auto 16px auto; 
            animation: globePulse 4s ease-in-out infinite;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        
        .logo svg {
            width: 56px;
            height: 56px;
        }
        
        @keyframes globePulse {
            0%, 100% { transform: scale(1) rotate(0deg); }
            25% { transform: scale(1.05) rotate(-2deg); }
            50% { transform: scale(1) rotate(0deg); }
            75% { transform: scale(1.05) rotate(2deg); }
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
            margin-bottom: 28px;
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
        
        @media (max-width: 480px) {
            .container {
                margin: 20px;
                padding: 36px 28px;
            }
            .logo { width: 48px; height: 48px; }
            .logo svg { width: 42px; height: 42px; }
            h1 { font-size: 22px; }
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
        <p>This is a testing version.<br>Please enter the access password to continue.</p>
        <form method="GET">
            <div class="form-group">
                <input type="password" name="pwd" placeholder="Enter password" autofocus required>
            </div>
            <button type="submit">Continue →</button>
        </form>
        <div class="footer">
            <span class="footer-icon">🌱</span>
            <span>Global Conservation Effort</span>
        </div>
    </div>
</body>
</html>`
	w.Write([]byte(html))
}
