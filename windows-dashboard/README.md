# Command Center — a local dashboard for your Windows PC

A single-command, offline-first control panel for your machine. It runs a small
FastAPI server on **127.0.0.1**, opens in your default browser and gives you:

- live **CPU / RAM / disk / GPU / network** monitoring over a WebSocket,
- a real **terminal** (PowerShell / CMD / Bash) with tabs, powered by xterm.js + ConPTY,
- **Git repository discovery** with branch, status, last commit, pull and one-click
  VS Code / Explorer / GitHub Desktop actions,
- a configurable **quick-launch grid**, **file explorer**, **markdown notes**,
- **system controls** (lock, sleep, restart, shutdown), maintenance tools
  (flush DNS, clear temp, empty Recycle Bin) and a **network toolbox**
  (adapters, ping, listening ports, port scan, speed test),
- optional **weather** and **Docker** panels that disappear when unavailable.

Everything is local: the only outbound requests are the weather widget, the
optional GitHub notifications and the speed test you trigger yourself.

---

## Quick start

1. Install **Python 3.10+** from [python.org](https://www.python.org/downloads/)
   and tick *"Add python.exe to PATH"*.
2. Double-click **`start.bat`**.

That's it. The script creates a local virtual environment (`.venv`), installs the
dependencies and starts the dashboard at <http://localhost:8420/>. If port 8420 is
busy it picks the next free one and tells you in the console.

Pass options straight through:

```bat
start.bat --no-browser            :: don't open a browser (e.g. for autostart)
start.bat --port 9000 --verbose   :: different port + debug logging
```

**Manual start** (any OS, if you prefer your own virtualenv):

```bash
cd windows-dashboard/backend
python -m pip install -r requirements.txt
python main.py
```

Stop the server with `Ctrl+C` or from *Settings → Advanced → Stop dashboard*.

> **Requirements:** Windows 10/11 for the full feature set. The dashboard also
> runs on macOS/Linux for development — Windows-only panels (Recycle Bin,
> Explorer restart, GitHub Desktop, ConPTY) are hidden or disabled automatically.

---

## Feature tour

### 1. System monitoring
Circular gauges for CPU/RAM/primary disk, per-core bars, CPU model and frequency,
GPU list (via WMI / `nvidia-smi` on Windows, `system_profiler` on macOS, `lspci`
on Linux), temperatures where the OS exposes them, battery state with time
remaining, swap/page-file usage, disk I/O and a rolling CPU/RAM sparkline.
A single background task samples everything every 2 s and streams it to all open
tabs through `/api/system/ws`.

### 2. GitHub Desktop + repositories
`~/Documents/GitHub`, `~/repos`, `~/projects`, `~/source/repos`, `~/dev` (plus any
folders you add in Settings) are scanned for `.git` folders — never descending
into `node_modules`, virtualenvs or nested repositories. For each repository you
get the current branch, ahead/behind counts, dirty/clean state with change counts,
last commit message/author/date and the remote URL (credentials masked).

Buttons: **pull** (`git pull --ff-only`), **open in VS Code**, **open folder**,
**open remote in browser**, plus a **GitHub Desktop** launcher that locates
`%LOCALAPPDATA%\GitHubDesktop\app-*\GitHubDesktop.exe` automatically.

Optional: paste a GitHub personal access token in Settings to show unread
notifications from the GitHub API. The token is stored locally in `config.json`
and never sent to the frontend.

### 3. Terminal
Real interactive sessions with xterm.js. On Windows the backend uses **ConPTY**
(`pywinpty`), so PowerShell colours, `vim`, `htop`, progress bars and full-screen
TUI apps work. Without `pywinpty` the app falls back to plain pipes (commands run,
but without a TTY). Multiple tabs, per-tab shell choice, resize handling, scrollback
replay on reconnect, `Ctrl+Shift+C/V` copy-paste, right-click paste, and a command
history sidebar built from what you type.

### 4. Quick launch
Pre-populated with VS Code, File Explorer, Task Manager, Notepad++, Chrome,
Discord, Spotify and Windows Terminal. Each tile can be edited (double-click or the
gear), and the editor can detect installed applications automatically. Paths may be
an executable on `PATH`, a full path, a folder, or a URL.

### 5. File Explorer Lite
Browse from Home/Desktop/Documents/Downloads/Pictures/Music/Videos or any drive.
Open files with their default application, preview text files in a modal, reveal in
Explorer, create folders, rename, and delete **to the Recycle Bin** (SHFileOperation
on Windows). Sizes, modification dates and item counts are shown; hidden files can
be toggled.

### 6. System controls
Lock / sleep / restart / shutdown with a confirmation dialog. Shutdown and restart
are scheduled 30 s out and can be aborted from the dashboard (or `shutdown /a`).
Maintenance: flush DNS, clear temporary files (older than 24 h, locked files
skipped), empty the Recycle Bin, restart Explorer. Every action is appended to
`logs/actions.log`.

### 7. Network
Adapters with IPv4/IPv6/MAC/link speed, Wi-Fi SSID + signal, gateway, DNS servers,
internal and external IP, ping with per-packet bars and loss/jitter statistics,
listening sockets with owning process (admin rights needed for names on Windows),
a basic localhost port scan, and a download speed test against Cloudflare.

### 8. Notes
Markdown notes stored as `.md` files in `notes/`, with tabs for several open notes,
autosave while you type, live preview, rename/delete, word count and a scratchpad
widget on the overview.

### 9. Weather (optional)
`wttr.in` current conditions and 3-day forecast. Auto-locates by IP unless you set
a city in Settings. If the machine is offline the card shows a retry button instead
of breaking.

### 10. Docker (optional)
Lists containers with state and live CPU/memory stats, start/stop/restart and a log
viewer. When Docker isn't installed (or the daemon is stopped) the card hides itself.

### UI details
Dark cyberpunk theme with a light alternative (toggle in the sidebar or Settings),
responsive card grid, drag cards by their header to reorder, drag the bottom-right
corner to resize (1–4 columns), maximise a card, toast notifications, a command
palette (`Ctrl+K`), keyboard shortcuts and colour-coded status (green / amber / red).

| Shortcut | Action |
| --- | --- |
| `Ctrl+K` | Command palette |
| `Alt+1` … `Alt+6` | Switch views |
| `Ctrl+Shift+T` | New terminal tab (terminal view) |
| `Ctrl+Shift+C/V` | Copy / paste in the terminal |
| `Ctrl+S` | Save the open note immediately |
| `F5` | Refresh statistics (not the page) |
| `Esc` | Close modal / palette / maximised card |

---

## Project structure

```
windows-dashboard/
├── backend/
│   ├── main.py                  # FastAPI app, static hosting, start-up
│   ├── requirements.txt
│   ├── routes/
│   │   ├── system.py            # stats REST + live websocket
│   │   ├── github.py            # repo scan / pull / desktop
│   │   ├── terminal.py          # PTY websocket
│   │   ├── launcher.py          # shortcuts
│   │   ├── files.py             # file explorer
│   │   ├── controls.py          # power + maintenance
│   │   ├── network.py           # ping, ports, speed test
│   │   ├── notes.py             # markdown notes
│   │   ├── weather.py           # wttr.in proxy
│   │   ├── docker.py            # optional Docker panel
│   │   └── config_api.py        # settings
│   └── services/
│       ├── monitor.py           # psutil sampling + history
│       ├── git_scanner.py       # discovery + GitPython helpers
│       ├── terminal_manager.py  # ConPTY / pty / pipe sessions
│       ├── docker_service.py    # docker CLI wrapper
│       ├── live.py              # websocket broadcast hub
│       ├── platform_utils.py    # launching apps, shell folders, recycle bin
│       ├── security.py          # localhost / origin guard
│       └── config.py            # config.json manager
├── frontend/
│   ├── index.html
│   ├── css/styles.css
│   ├── js/  api.js app.js system.js github.js launcher.js terminal.js
│   │        files.js network.js notes.js docker.js weather.js settings.js
│   ├── vendor/                  # xterm.js, marked, DOMPurify (offline copies)
│   └── assets/favicon.svg
├── config.json                  # settings (created/updated at runtime)
├── notes/                       # markdown notes (Welcome.md included)
├── logs/                        # created at runtime: dashboard.log, actions.log
└── start.bat
```

### API overview

A self-contained API reference (no CDN, works offline) is served at **`/api/docs`**;
the raw schema is at `/api/openapi.json`.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/system/stats` / `/api/system/info` | snapshot / static info + feature flags |
| WS | `/api/system/ws` | push stats every 2 s (`refresh`, `ping`) |
| GET | `/api/github/repos` | scan (cached) |
| POST | `/api/github/pull` `open-code` `open-folder` `open-desktop` | repo actions |
| GET | `/api/github/notifications` | GitHub API (token required) |
| WS | `/api/terminal/ws/{id}` | raw PTY stream |
| GET/POST/PUT/DELETE | `/api/launcher/shortcuts…` | shortcut CRUD |
| GET | `/api/files/list` `roots` `preview` | browse |
| POST | `/api/files/mkdir` `rename` `delete` `open` | file operations |
| POST | `/api/controls/action` | lock/sleep/restart/shutdown/abort |
| POST | `/api/controls/flush-dns` `clear-temp` `empty-recycle-bin` | maintenance |
| POST | `/api/network/ping` `portscan` `speedtest` | network tools |
| GET/PUT/POST/DELETE | `/api/notes…` | notes CRUD |
| GET | `/api/weather`, `/api/docker` | optional panels |
| GET/PATCH | `/api/config` | read/update settings |

---

## Configuration (`config.json`)

The file is created on first run and merged with defaults on upgrade, so new keys
appear automatically. Edit it while the dashboard is stopped, or use Settings.

```jsonc
{
  "app":     { "host": "127.0.0.1", "port": 8420, "open_browser": true, "refresh_seconds": 2 },
  "ui":      { "theme": "dark", "accent": "cyan", "compact": false,
               "card_order": ["system", "…"], "card_spans": {}, "hidden_cards": [],
               "active_view": "overview" },
  "launcher":{ "shortcuts": [ { "id", "name", "path", "args", "icon", "color" } ] },
  "repos":   { "scan_paths": ["C:\\Users\\you\\repos"],
               "max_depth": 3, "max_repos": 200, "cache_seconds": 20,
               "notifications": { "enabled": false, "token": "", "refresh_seconds": 120 } },
  "paths":   { "vscode": "code", "github_desktop": "" },
  "terminal":{ "default_shell": "auto", "font_size": 13, "font_family": "…" },
  "files":   { "show_hidden": false, "delete_to_recycle_bin": true, "start_path": "" },
  "weather": { "enabled": true, "location": "", "units": "metric", "refresh_minutes": 15 },
  "docker":  { "enabled": true },
  "network": { "speedtest": true }
}
```

Environment variables: `DASHBOARD_HOST`, `DASHBOARD_PORT`, `DASHBOARD_NO_BROWSER=1`,
`DASHBOARD_ALLOWED_HOSTS` (comma separated extra hostnames) and
`DASHBOARD_ALLOWED_SUFFIX` (defaults to `.e2b.app` for hosted previews).

---

## Security notes

- The server binds to **127.0.0.1** by default; nothing on your LAN can reach it.
- A middleware rejects requests whose `Host` header is not localhost (DNS-rebinding
  protection) and blocks cross-site requests via the `Origin` header. Websocket
  handshakes are checked the same way.
- No credentials are ever sent to the frontend: the GitHub token is stored in
  `config.json` and only exposed as `has_token: true`.
- Power actions need an explicit confirmation and are logged.
- File deletion defaults to the Recycle Bin.
- The dashboard has no authentication — it trusts the local user, like any
  desktop app. Don't bind it to `0.0.0.0` on an untrusted network.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `start.bat` closes instantly | Run it from a Command Prompt to read the error; check Python is 3.10+. |
| `pip` install fails | `python -m pip install -U pip`, or delete `.venv` and rerun. |
| Port already in use | The app auto-picks the next port — check the console banner. |
| Terminal shows `[dashboard] ConPTY unavailable` | `pip install pywinpty` (needs the MSVC build tools fallback: upgrade pip first). |
| No GPU stats | Integrated GPUs only expose a name; NVIDIA needs `nvidia-smi` on `PATH`. |
| CPU temperature `not exposed` | Windows often hides thermal sensors; other tools need a kernel driver. |
| Weather shows "unreachable" | No internet, or `wttr.in` is blocked — the rest of the app works offline. |
| GitHub notifications fail | Token needs the `notifications` scope; check the message in the notification modal. |
| Recycle Bin / Explorer actions fail | These require Windows; admin rights may be needed. |
| Empty ports table on Windows | Run the dashboard as administrator to see process names. |

Logs live in `logs/dashboard.log`; the last system actions are in
`logs/actions.log`.

## Development

- `python main.py --reload` restarts on backend changes.
- The frontend is plain HTML/CSS/JS (no build step). Libraries are vendored in
  `frontend/vendor/` so the app works fully offline.
- `GET /api/docs` shows the OpenAPI schema; `/api/health` is a cheap liveness probe.
- macOS/Linux support exists for development: PTYs use `pty`, apps launch through
  `open`/`xdg-open`, and Windows-only endpoints report "not available".

---

*Vendored libraries: xterm.js 5.5.0 (MIT), xterm-addon-fit 0.10.0 (MIT),
marked 12.0.2 (MIT), DOMPurify 3.1.6 (Apache-2.0/MIT). Licences are kept in
`frontend/vendor/`.*
