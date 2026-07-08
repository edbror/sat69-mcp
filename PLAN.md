# PLAN — sat69-mcp

MCP server para consultar las listas del SAT (Art. 69 y 69-B del CFF). FastMCP,
SQLite+FTS5, Turso, deploy en Render.

## Fases

- [x] **1. Core** — pipeline (fetch/parse 69 y 69b), database (SQLite+FTS5), risk.
- [x] **2. Tools MCP** — verificar_rfc, verificar_lote, buscar_nombre, estado_datos, actualizar_datos.
- [x] **3. Transportes** — stdio (server.py) + HTTP (web.py) con OAuth/bearer.
- [x] **4. Tests** — test_pipeline, test_risk (13 passing).
- [x] **5. Repo + Docker** — git init, Dockerfile, compose.yaml, GitHub.
- [ ] **6. Deploy Render** — verificar deploy verde + cron (GitHub Actions).
