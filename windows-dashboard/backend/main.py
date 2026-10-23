"""Windows Dashboard - FastAPI entry point.

Run with ``python main.py`` (or the bundled ``start.bat``).  The server binds to
127.0.0.1 only, opens the default browser and exposes:

    /                the dashboard UI
    /api/...         REST endpoints
    /api/system/ws   live system statistics websocket
    /api/terminal/ws/{session_id}  interactive terminal websocket
    /docs            automatically generated API documentation
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import webbrowser
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Make `import services...` work when the file is executed directly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.middleware.gzip import GZipMiddleware  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from routes import config_api, controls, docker, files, github, launcher, network, notes, system, terminal, weather  # noqa: E402
from services.config import BACKEND_DIR, CONFIG_PATH, FRONTEND_DIR, LOG_DIR, VERSION, config  # noqa: E402
from services.live import hub  # noqa: E402
from services.security import LocalOnlyMiddleware  # noqa: E402
from services.terminal_manager import terminal_manager  # noqa: E402

log = logging.getLogger("dashboard")


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
def setup_logging(verbose: bool = False) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
        root.addHandler(stream)
    try:
        rotating = RotatingFileHandler(LOG_DIR / "dashboard.log", maxBytes=1_000_000,
                                       backupCount=2, encoding="utf-8")
        rotating.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(rotating)
    except OSError:
        pass
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


# --------------------------------------------------------------------------- #
# App factory
# --------------------------------------------------------------------------- #
def create_app() -> FastAPI:
    config.ensure_dirs()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging()
        log.info("Windows Dashboard %s starting (config: %s)", VERSION, CONFIG_PATH)
        await hub.start()
        try:
            yield
        finally:
            log.info("Shutting down...")
            await hub.stop()
            terminal_manager.close_all()

    app = FastAPI(
        title="Windows Dashboard",
        version=VERSION,
        description="Local personal command center for Windows",
        docs_url=None,  # replaced with an offline-friendly page below
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(LocalOnlyMiddleware)

    for module in (system, github, terminal, launcher, files, controls, network,
                   notes, weather, docker, config_api):
        app.include_router(module.router)

    # ------------------------------------------------------------------ #
    # Errors
    # ------------------------------------------------------------------ #
    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        message = first.get("msg", "Invalid request")
        field = ".".join(str(p) for p in first.get("loc", []))
        return JSONResponse({"detail": f"{field}: {message}" if field else message}, status_code=422)

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception):
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": f"Internal error: {exc}"}, status_code=500)

    # ------------------------------------------------------------------ #
    # Meta endpoints
    # ------------------------------------------------------------------ #
    @app.get("/api/health", tags=["meta"])
    def health() -> dict:
        return {
            "ok": True,
            "version": VERSION,
            "clients": hub.client_count,
            "config": str(CONFIG_PATH),
            "platform": sys.platform,
            "pid": os.getpid(),
        }

    @app.post("/api/app/shutdown", tags=["meta"])
    def shutdown_server() -> dict:
        """Stop the dashboard server (does not affect other applications)."""
        log.info("Shutdown requested from the UI")
        thread = threading.Timer(0.6, _stop_server)
        thread.daemon = True
        thread.start()
        return {"ok": True, "message": "Dashboard is shutting down."}


    # ------------------------------------------------------------------ #
    # Offline API reference (Swagger UI would need a CDN)
    # ------------------------------------------------------------------ #
    @app.get("/api/docs", include_in_schema=False)
    def api_docs() -> HTMLResponse:
        return HTMLResponse(DOCS_HTML)

    # ------------------------------------------------------------------ #
    # Static frontend
    # ------------------------------------------------------------------ #
    assets = FRONTEND_DIR / "assets"
    for folder, mount in (
        ("css", "/css"),
        ("js", "/js"),
        ("vendor", "/vendor"),
        ("assets", "/assets"),
    ):
        directory = FRONTEND_DIR / folder
        if directory.is_dir():
            app.mount(mount, StaticFiles(directory=directory), name=folder)

    index_file = FRONTEND_DIR / "index.html"

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(index_file, media_type="text/html")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        icon = assets / "favicon.svg"
        if icon.exists():
            return FileResponse(icon, media_type="image/svg+xml")
        return JSONResponse({}, status_code=204)

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest():
        file = FRONTEND_DIR / "manifest.webmanifest"
        return FileResponse(file) if file.exists() else JSONResponse({}, status_code=404)

    return app


def _stop_server() -> None:
    """Ask uvicorn to exit cleanly (SIGINT triggers the normal shutdown path)."""
    try:
        import signal

        signal.raise_signal(signal.SIGINT)
    except Exception:  # noqa: BLE001
        os._exit(0)



DOCS_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Command Center · API</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 14px/1.55 "Segoe UI", system-ui, sans-serif; color: #e6edf7;
         background: radial-gradient(900px 500px at 15% -10%, rgba(34,211,238,.14), transparent 60%), #05070d; }
  main { max-width: 980px; margin: 0 auto; padding: 40px 22px 80px; }
  h1 { font-size: 26px; margin: 0 0 4px; }
  .sub { color: #93a4bb; margin-bottom: 26px; }
  .sub a { color: #22d3ee; }
  h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .12em; color: #7f93ab;
       margin: 30px 0 10px; border-bottom: 1px solid rgba(120,160,200,.16); padding-bottom: 6px; }
  .row { display: flex; gap: 12px; align-items: baseline; padding: 9px 12px; border-radius: 9px;
         border: 1px solid transparent; }
  .row:hover { background: rgba(120,160,200,.06); border-color: rgba(120,160,200,.16); }
  .m { flex: 0 0 62px; font: 11px/1.6 ui-monospace, Consolas, monospace; text-align: center;
       border-radius: 5px; padding: 2px 0; letter-spacing: .04em; }
  .get { background: rgba(34,211,238,.16); color: #67e8f9; }
  .post { background: rgba(52,211,153,.16); color: #6ee7b7; }
  .put { background: rgba(251,191,36,.16); color: #fcd34d; }
  .patch { background: rgba(167,139,250,.18); color: #c4b5fd; }
  .delete { background: rgba(248,113,113,.16); color: #fca5a5; }
  .ws { background: rgba(244,114,182,.16); color: #f9a8d4; }
  code { font-family: ui-monospace, Consolas, monospace; font-size: 13px; color: #dbe7f7; }
  .summary { color: #93a4bb; font-size: 12.5px; }
  .empty { color: #7f93ab; }
  footer { margin-top: 40px; color: #66788f; font-size: 12px; }
  input { width: 100%; max-width: 380px; margin: 6px 0 18px; padding: 9px 12px; border-radius: 8px;
          border: 1px solid rgba(120,160,200,.2); background: rgba(6,10,18,.6); color: inherit; }
</style></head>
<body><main>
  <h1>Command Center API</h1>
  <p class="sub">Offline reference generated from <a href="/api/openapi.json">/api/openapi.json</a>
     · <a href="/">back to dashboard</a></p>
  <input id="filter" placeholder="Filter endpoints…" aria-label="Filter endpoints">
  <div id="out" class="empty">Loading…</div>
  <footer>All endpoints are local to this machine. State-changing requests require same-origin.</footer>
</main>
<script>
(function () {
  const out = document.getElementById("out");
  const filter = document.getElementById("filter");
  let groups = [];
  const order = ["system", "github", "terminal", "launcher", "files", "controls",
                 "network", "notes", "weather", "docker", "config", "meta"];

  function label(method) {
    return method === "get" ? "GET" : method === "post" ? "POST" : method === "websocket" ? "WS"
      : method.toUpperCase();
  }

  function render() {
    const needle = filter.value.trim().toLowerCase();
    out.className = "";
    out.textContent = "";
    let shown = 0;
    groups.forEach((group) => {
      const items = group.items.filter((item) =>
        !needle || (item.path + " " + item.summary).toLowerCase().includes(needle));
      if (!items.length) return;
      shown += items.length;
      const h = document.createElement("h2");
      h.textContent = group.tag;
      out.append(h);
      items.forEach((item) => {
        const row = document.createElement("div");
        row.className = "row";
        const m = document.createElement("span");
        m.className = "m " + (item.method === "websocket" ? "ws" : item.method);
        m.textContent = label(item.method);
        const body = document.createElement("div");
        const code = document.createElement("code");
        code.textContent = item.path;
        const p = document.createElement("div");
        p.className = "summary";
        p.textContent = item.summary;
        body.append(code, p);
        row.append(m, body);
        out.append(row);
      });
    });
    if (!shown) {
      const p = document.createElement("p");
      p.className = "empty";
      p.textContent = "No matching endpoints.";
      out.append(p);
    }
  }

  fetch("/api/openapi.json").then((r) => r.json()).then((spec) => {
    const map = new Map();
    Object.entries(spec.paths || {}).forEach(([path, methods]) => {
      Object.entries(methods).forEach(([method, op]) => {
        const tag = (op.tags && op.tags[0]) || "meta";
        if (!map.has(tag)) map.set(tag, []);
        map.get(tag).push({ path, method, summary: op.summary || "" });
      });
    });
    groups = Array.from(map.entries())
      .map(([tag, items]) => ({ tag, items }))
      .sort((a, b) => rank(a.tag) - rank(b.tag));

  function rank(tag) {
    const index = order.indexOf(tag);
    return index === -1 ? 99 : index;
  }
    render();
  }).catch(() => {
    out.textContent = "Could not load the OpenAPI schema.";
  });
  filter.addEventListener("input", render);
})();
</script></body></html>
"""

# --------------------------------------------------------------------------- #
# Boot helpers
# --------------------------------------------------------------------------- #
def find_available_port(host: str, preferred: int) -> int:
    """Return ``preferred`` if free, otherwise the next free port."""
    for candidate in range(preferred, preferred + 25):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            # SO_REUSEADDR on Windows can report a busy port as free.
            if os.name != "nt":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, candidate))
            except OSError:
                continue
            return candidate
    raise RuntimeError(f"No free port found near {preferred}")


def _open_browser_later(url: str, delay: float = 1.2) -> None:
    def open_it() -> None:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass

    timer = threading.Timer(delay, open_it)
    timer.daemon = True
    timer.start()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Windows Dashboard server")
    parser.add_argument("--host", default=os.environ.get("DASHBOARD_HOST") or config.get("app.host", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("DASHBOARD_PORT") or config.get("app.port", 8420)))
    parser.add_argument("--no-browser", action="store_true", help="do not open the default browser")
    parser.add_argument("--verbose", action="store_true", help="enable debug logging")
    parser.add_argument("--reload", action="store_true", help="auto-reload on code changes (development)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)

    host = args.host
    port = find_available_port(host, args.port)
    if port != args.port:
        log.warning("Port %s is busy, using %s instead", args.port, port)

    # Persist the port we actually use so the UI/README know where to look.
    if config.get("app.port") != port:
        try:
            config.set("app.port", port)
        except OSError:
            pass

    url = f"http://{'localhost' if host in ('0.0.0.0', '127.0.0.1') else host}:{port}/"
    banner = (
        "\n"
        "  ==============================================\n"
        f"    COMMAND CENTER  v{VERSION}\n"
        f"    {url}\n"
        "    Press Ctrl+C to stop the server\n"
        "  ==============================================\n"
    )
    print(banner)

    wants_browser = (
        not args.no_browser
        and os.environ.get("DASHBOARD_NO_BROWSER") not in ("1", "true")
        and bool(config.get("app.open_browser", True))
    )
    if wants_browser:
        _open_browser_later(url)

    if args.reload:
        # Reload mode needs an import string.
        os.chdir(BACKEND_DIR)
        uvicorn.run("main:app", host=host, port=port, reload=True, log_level="info")
        return 0

    try:
        uvicorn.run(app, host=host, port=port, log_level="debug" if args.verbose else "info",
                    access_log=args.verbose, timeout_keep_alive=30)
    except KeyboardInterrupt:
        pass
    finally:
        terminal_manager.close_all()
    return 0


app = create_app()

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0) from None
