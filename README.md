# 5MP Conservation Monitoring Dashboard

Interactive 3D globe for monitoring 162 African protected areas. Tracks fire detection, deforestation, settlements, and patrol activity using satellite data.

**Live URL**: https://five-megapixel-conservation.exe.xyz:8000/?pwd=test2026

---

## For LLM Agents

🚨 **START HERE**: Read `QUICK_START_AGENTS.md` first (2-minute orientation)

Then navigate to:
- **Data Flow Maps**: `docs/DATA_FLOW.md` - How data moves through the system
- **Quick Tasks**: `docs/QUICK_TASKS.md` - Copy-paste solutions for common modifications
- **Mental Models**: `docs/MENTAL_MODEL.md` - Understanding the 17K-line single-page app
- **Architecture**: `docs/ARCHITECTURE_DECISIONS.md` - Why things are built this way
- **Reference**: `AGENTS.md` - Comprehensive tables, APIs, credentials

---

## For Humans

- **Overview**: `docs/README.md` - Project introduction
- **Setup**: `docs/INSTALL.md` - Installation guide
- **API**: `docs/API.md` - API reference
- **Database**: `docs/DATABASE.md` - Schema documentation

## Building and Running

Build with `make build`, then run `./server`. The server listens on port 8000 by default.

The Makefile:
- Embeds git commit hash as version (`-X srv.exe.dev/srv.Version=...`)
- Generates `.git-commits.txt` for the version history modal in UI
- Click the version number in the footer to see recent commits

## Running as a systemd service

To run the server as a systemd service:

```bash
# Install the service file
sudo cp srv.service /etc/systemd/system/srv.service

# Reload systemd and enable the service
sudo systemctl daemon-reload
sudo systemctl enable srv.service

# Start the service
sudo systemctl start srv

# Check status
systemctl status srv

# View logs
journalctl -u srv -f
```

To restart after code changes:

```bash
make build
sudo systemctl restart srv
```

## Authorization

exe.dev provides authorization headers and login/logout links
that this template uses.

When proxied through exed, requests will include `X-ExeDev-UserID` and
`X-ExeDev-Email` if the user is authenticated via exe.dev.

## Database

This template uses sqlite (`db.sqlite3`). SQL queries are managed with sqlc.

## Code layout

- `cmd/srv`: main package (binary entrypoint)
- `srv`: HTTP server logic (handlers)
- `srv/templates`: Go HTML templates
- `db`: SQLite open + migrations (001-base.sql)

## Licence

Code: MIT (see `LICENSE`). Data sources and their terms: `srv/license.go`, served at `/licenses` and `/api/licenses`. The app is non-commercial; several imagery/data terms depend on that.
