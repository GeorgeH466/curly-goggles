# curly-goggles

A local, browser-based command center for Windows PCs.

The application lives in **[`windows-dashboard/`](windows-dashboard/)** — a FastAPI
backend plus a plain HTML/CSS/JavaScript frontend that monitors your machine, runs
terminals, scans Git repositories, browses files and controls the system.

## Run it

```bat
cd windows-dashboard
start.bat
```

The dashboard opens at <http://localhost:8420/>.

See [`windows-dashboard/README.md`](windows-dashboard/README.md) for the full
feature list, configuration reference and troubleshooting.
