# MCP SAT 69 / 69-B · WATR

Servidor **MCP (Model Context Protocol)** en **Python / FastMCP** para consultar las listas públicas del SAT:

- **Artículo 69-B del CFF (EFOS)** — operaciones simuladas: *Presunto, Desvirtuado, Definitivo, Sentencia Favorable*.
- **Artículo 69 del CFF** — situación fiscal firme: *firmes, exigibles, no localizados, cancelados, condonados*.

Misma arquitectura que el MCP de movilizaciones de la SSC-CDMX: FastMCP con transporte **stdio + Streamable HTTP**, **OAuth 2.1 (WorkOS AuthKit)** con fallback a **bearer estático**, persistencia en **Turso (libSQL)**, **proxy de descarga opcional**, y despliegue en **Render** con cron externo (GitHub Actions). Sin OCR: los CSV del SAT ya vienen estructurados.

## Tools

| Tool | Qué hace |
|------|----------|
| `verificar_rfc` | Verifica un RFC → **veredicto de riesgo** (`CRITICO`…`LIMPIO`). |
| `verificar_lote` | Valida hasta 500 RFCs; devuelve sólo hallazgos por severidad. |
| `buscar_nombre` | Búsqueda por nombre/razón social (FTS5, insensible a acentos). |
| `estado_datos` | Vigencia declarada por el SAT, conteos y última importación. |
| `actualizar_datos` | Descarga + sincroniza los listados (idempotente por hash). |

**Riesgo:** `CRITICO` (EFOS definitivo) · `ALTO` (EFOS presunto) · `MEDIO` (69 firme/exigible/no localizado) · `BAJO` (desvirtuado/sentencia favorable) · `INFORMATIVO` (69 cancelado/condonado) · `LIMPIO`.

> Los resultados reflejan la última importación de los archivos públicos del SAT. No constituyen asesoría fiscal ni legal.

## Arquitectura

```
CSV del SAT (Latin-1)                     ┌────────── FastMCP ──────────┐
   │  fetcher (httpx + proxy opcional)    │ verificar_rfc / _lote        │
   ▼                                      │ buscar_nombre / estado_datos │
 pipeline (parse 69 / 69b)  ──►  SQLite ──┤ actualizar_datos             │
   │   (FTS5 unicode61, triggers)  ▲  │   └──────────┬──────────────────┘
   ▼                               │  │   stdio (server.py) + HTTP (web.py)
 Turso (libSQL, durable) ◄── push  │  └► pull al arranque      │
                                   └─────────────────── /health /refresh /reload
```

- **`server.py`** — FastMCP (stdio) + tools + AuthKit.
- **`web.py`** — Starlette/uvicorn (HTTP): `/health` (abierto), `/refresh` y `/reload` (bearer M2M), `/mcp` (OAuth o bearer).
- **`pipeline.py`** — descarga → SHA-256 (omite si no cambió) → parse Latin-1 → reemplazo en SQLite → push a Turso.
- **`database.py`** — SQLite + FTS5 con triggers; RFC por índice B-tree (camino caliente).
- **`turso.py`** — sync durable Turso ↔ local.
- **`risk.py`** — normalización de RFC + árbol de veredicto (69-B manda sobre 69).

## Instalación local

```bash
cd sat69-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Primera ingesta (descarga ~22 MB; el 69 son ~½ millón de filas)
python -c "from sat69 import database as db, config; db.init_db(config.settings.db_path)"
python -c "from sat69.pipeline import process_import; print(process_import())"

# Probar
pytest -q
```

### Conectar en Claude Desktop / Cowork (stdio)

```json
{
  "mcpServers": {
    "sat69": { "command": "sat69-mcp" }
  }
}
```

(o `"command": "python", "args": ["-m", "sat69"]` con el venv activo.)

## Despliegue en Render

`render.yaml` provisiona el servicio web con runtime Python nativo (sin Docker):

1. Sube el repo a GitHub y crea un Blueprint en Render apuntando a `render.yaml`.
2. Variables (marcadas `sync:false`): `MCP_API_KEY` (bearer), opcional `AUTHKIT_DOMAIN`+`BASE_URL` (OAuth), `TURSO_DATABASE_URL`+`TURSO_AUTH_TOKEN`.
3. Endpoint MCP: `POST https://<servicio>.onrender.com/mcp`.

### Auth (dos modos, igual que movilizaciones)

- **Bearer estático** (`MCP_API_KEY`): simple, protege `/mcp`, `/refresh`, `/reload`.
- **OAuth 2.1 (WorkOS AuthKit)**: define `AUTHKIT_DOMAIN` + `BASE_URL` y `/mcp` pasa a OAuth con Dynamic Client Registration; el bearer sigue protegiendo los endpoints M2M.

### Refresco automático

`.github/workflows/refresh.yml` hace `POST /refresh` diario (11:30 UTC ≈ 05:30 CDMX). Secrets del repo: `RENDER_BASE_URL`, `MCP_API_KEY`. Manual: `workflow_dispatch` (con `force`).

## Persistencia (Turso)

SQLite local (efímero en Render, `/tmp`) atiende las consultas; Turso es el almacén durable que sobrevive redeploys. Al arrancar se hace pull de Turso; tras cada `actualizar_datos`/`/refresh` se hace push. Sin `TURSO_*`, corre en modo local puro.

## Proxy de descarga (opcional)

`FETCH_PROXY` o `DATAIMPULSE_*` enrutan **sólo** la descarga de los CSV. En pruebas el SAT **no** bloqueó IPs de datacenter (descargas 200 directas), así que normalmente no hace falta; se conserva por paridad y resiliencia.

## Fuentes de datos (SAT · Datos Abiertos)

- 69-B: `Listado_Completo_69-B.csv` (~14 k registros, 20 columnas, header en línea 3).
- 69: `Firmes.csv`, `Cancelados.csv`, `NoLocalizados.csv`, `Exigibles.csv`, `Sentencias.csv`, `Condonados.csv` (~½ millón de registros, 6 columnas). URLs en `config.py`.
