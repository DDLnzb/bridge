# Bridge — Public Repository Overlay

Dieses Verzeichnis ist **kein** Teil des Bridge-Quellcodes. Es enthält die Dateien, die
zusätzlich in das public Repo `DDLnzb/bridge` wandern, wenn der Sync-Job in
`.github/workflows/ci.yml` den Code aus `bridge/` dorthin spiegelt.

## Zweck

`DDLnzb/bridge` soll den Bridge-Quellcode **und** das Container-Image öffentlich
bereitstellen. Der Code bleibt im Monorepo `weedo078/DDLnzb` die Quelle der Wahrheit —
das public Repo ist ein Spiegel, kein zweiter Editier-Ort.

## Was der Sync überträgt

Der Sync-Job überträgt **ausschließlich** den Inhalt von `bridge/` in den Repo-Root und
legt dieses Overlay zusätzlich darüber:

| Overlay-Datei | Zweck |
|---|---|
| `LICENSE` | MIT-Lizenz — deckt sich mit der Lizenz des Monorepos |
| `SECURITY.md` | Meldeweg für Schwachstellen in einem public Repo |
| `.gitignore` | schützt vor versehentlichem Commit von `.env` und State-Dateien |
| `.github/workflows/` | eigene CI im public Repo (Tests + Image-Build) |

## Was der Sync **nicht** überträgt

Der Sync filtert hart. Diese Pfade bleiben im Monorepo und werden nie veröffentlicht:

- `bridge/.env` — enthält echte Zugangsdaten (`MYJD_PASSWORD`, `MYJD_APP_KEY`,
  `DDLNZB_API_KEY`)
- `bridge/ddlnzb_bridge_state.json` — Laufzeit-Zustand mit Download-Historie
- `bridge/**/__pycache__/` — Bytecode

`bridge/.env.example` wird **mit** übertragen: es enthält nur Platzhalter.

## Pflege

Änderungen an Dateien in diesem Ordner wirken erst nach dem nächsten Sync, also nach
einem Push von `bridge/` oder dieses Overlays nach `main`. Der Sync läuft im Job
`sync-bridge-public` der Datei `.github/workflows/ci.yml`.
