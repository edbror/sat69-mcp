# Checklist de despliegue — MCP SAT 69/69-B (Render + Turso + AuthKit)

Calcado del proceso ya probado en `ssc-movilizaciones-mcp`, adaptado a SAT69.

**Ventaja vs. movilizaciones:** el SAT **no bloquea IPs de datacenter** (descargas 200 directas desde el sandbox) y **no hay OCR**. Por eso el cron puede correr en la nube sin proxy residencial, y `/refresh` es barato (segundos, no OCR de páginas). El `FETCH_PROXY` queda como opción de resiliencia, no requisito.

Leyenda: ⬜ acción tuya (consola/cuenta) · 🟦 acción de código/CLI · 🔎 verificación.

---

## 0. Prerrequisitos
- ⬜ Cuentas: GitHub, Render, Turso. (WorkOS sólo si activas OAuth en la fase 6.)
- 🟦 CLI de Turso instalado: `curl -sSfL https://get.tur.so/install.sh | bash`
- 🟦 Genera el bearer M2M (guárdalo en tu gestor de secretos):
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`

---

## 1. Repositorio
- 🟦 `cd sat69-mcp && git init && git add . && git commit -m "SAT69 MCP v1"`
- ⬜ Crea el repo en GitHub (sugerido `github.com/edbror/sat69-mcp`, privado) y `git push`.
- 🔎 Confirma que `.env` **no** se subió (está en `.gitignore`).

---

## 2. Turso (persistencia durable)
- 🟦 `turso db create sat69 --group default` (región US; p. ej. `aws-us-east-1`).
- 🟦 URL:   `turso db show sat69 --url`            → `libsql://sat69-<org>.turso.io`
- 🟦 Token: `turso db tokens create sat69`         → `TURSO_AUTH_TOKEN`
- ℹ️ El código ya normaliza `libsql://`→`https://` (hrana-over-HTTP), como en movilizaciones. No necesitas tocar nada.
- ℹ️ El esquema en Turso se crea solo en el primer `push_to_turso()`.

---

## 3. Render (servicio web)
- ⬜ New → **Blueprint** → conecta el repo (usa el `render.yaml` incluido).
- ⬜ Setea las variables `sync:false` en el dashboard:
  - `MCP_API_KEY` = el bearer del paso 0
  - `TURSO_DATABASE_URL` = la URL del paso 2
  - `TURSO_AUTH_TOKEN` = el token del paso 2
  - (deja `AUTHKIT_DOMAIN` vacío por ahora → arranca en modo bearer)
  - `BASE_URL` = `https://sat69-mcp.onrender.com` (ajusta al nombre real del servicio)
- ⬜ Deploy. Espera "Live".
- 🔎 `curl https://sat69-mcp.onrender.com/health` → responde `ok / status: no_data` (aún sin datos).
- ℹ️ Plan free hiberna: el primer request tras inactividad tarda ~30–60 s (cold start).

---

## 4. Primera ingesta
- 🟦 Dispara la carga (descarga ~22 MB; el 69 son ~½ millón de filas):
  ```bash
  curl -sS -X POST https://sat69-mcp.onrender.com/refresh \
    -H "Authorization: Bearer <MCP_API_KEY>"
  ```
- 🔎 Espera `{"success": true, "rows_importados": <N>, ...}`.
- 🔎 `curl .../health` ahora muestra los conteos de 69 y 69b.
- 🔎 En Turso: `turso db shell sat69 "SELECT COUNT(*) FROM registros_69b;"` → ~14 k.

> Alternativa sin esperar a Render: corre la ingesta local (`process_import()`) con `TURSO_*` en tu `.env` y sólo haz `POST /reload` en Render para jalar de Turso.

---

## 5. Refresco automático (GitHub Actions)
- ⬜ En el repo → Settings → Secrets and variables → Actions:
  - `RENDER_BASE_URL` = `https://sat69-mcp.onrender.com`
  - `MCP_API_KEY` = el mismo bearer
- 🔎 Actions → **Refresh SAT 69/69-B** → *Run workflow* (manual) → debe dar HTTP 200.
- ℹ️ Cron ya configurado a 11:30 UTC (05:30 CDMX) en `.github/workflows/refresh.yml`.
- ℹ️ Como el SAT no bloquea datacenter, aquí **no** necesitas `FETCH_PROXY` (a diferencia de movilizaciones).

---

## 6. Conectar clientes

### Claude Code (CLI)
```bash
claude mcp add --transport http sat69 \
  https://sat69-mcp.onrender.com/mcp \
  --header "Authorization: Bearer <MCP_API_KEY>"
```

### `.mcp.json` del proyecto / Cowork
```json
{
  "mcpServers": {
    "sat69": {
      "type": "http",
      "url": "https://sat69-mcp.onrender.com/mcp",
      "headers": { "Authorization": "Bearer <MCP_API_KEY>" }
    }
  }
}
```

### Claude Desktop
Settings → Connectors → Add custom connector (misma URL + header). Si tu versión sólo acepta locales, usa el puente `mcp-remote` (igual patrón que en `INTEGRATION.md` de movilizaciones).

- 🔎 Prueba: `verificar_rfc {"rfc":"AAA080808HL8"}` → riesgo `BAJO` (Sentencia Favorable).
- 🔎 `estado_datos {}` → vigencia y conteos.

---

## 7. OAuth 2.1 (WorkOS AuthKit) — opcional, recomendado para prod
Mismo orden que dejaste pendiente en movilizaciones (evita downtime):
- ⬜ Crea proyecto en **WorkOS AuthKit**, **habilita DCR** (Dynamic Client Registration).
- ⬜ Copia el **AuthKit domain** (`https://<tu-proyecto>.authkit.app`).
- ⬜ En Render: `AUTHKIT_DOMAIN=<domain>` y confirma `BASE_URL=https://sat69-mcp.onrender.com` → redeploy.
- 🔎 Verifica la PRM (RFC 9728), que va **por-recurso**:
  `curl https://sat69-mcp.onrender.com/.well-known/oauth-protected-resource/mcp`
- 🔎 Reconecta un cliente MCP: debe disparar el flujo OAuth automáticamente (ya no bearer en `/mcp`).
- 🟦 **Rota el `MCP_API_KEY`** después de activar OAuth (en OAuth sólo protege `/refresh` y `/reload`).

---

## 8. Verificación end-to-end (curls)
```bash
BASE=https://sat69-mcp.onrender.com
KEY=<MCP_API_KEY>
curl -sS $BASE/health                                             # 200, sin auth
curl -sS -o /dev/null -w "%{http_code}\n" $BASE/refresh           # 401 sin bearer
curl -sS -X POST $BASE/refresh -H "Authorization: Bearer $KEY"    # 200 success
```
- 🔎 Inspector: `npx @modelcontextprotocol/inspector` apuntando a `$BASE/mcp` (+ header).

---

## 9. Seguridad
- 🔐 El bearer es una credencial: **nunca** lo comitees. (En el repo de movilizaciones quedó un token en `INTEGRATION.md` en claro — para SAT69 mantenlo sólo en Render/GitHub Secrets y rótalo si se expone.)
- 🔐 En prod, activa OAuth (fase 7) y deja el bearer sólo para los endpoints M2M.
- 🔐 Datos: son públicos por mandato del Art. 69 CFF; no hay PII sensible.

---

## 10. Lanzamiento público — OAuth + dominio de marca + directorio MCP

**Meta:** SAT69 listado en el registro MCP oficial, conectable por cualquiera vía
`https://sat69.watr.mx/connect`, con OAuth (AuthKit) dando identidad por-usuario y
el gate freemium (1 consulta/día gratis) contando de verdad en Turso.

Corre en este orden exacto (evita downtime y host-mismatch de OAuth):

### 10.1 Turso — que el gate cuente (hoy cae-abierto sin esto)
- 🟦 `turso db create sat69 --group default` (o reusa la existente).
- 🟦 `turso db show sat69 --url` → `TURSO_DATABASE_URL` · `turso db tokens create sat69` → `TURSO_AUTH_TOKEN`.

### 10.2 WorkOS AuthKit
- ⬜ Crea proyecto en **WorkOS AuthKit** y **habilita DCR** (Dynamic Client Registration — sin DCR los clientes MCP no se auto-registran).
- ⬜ Copia el **AuthKit domain**: `https://<tu-proyecto>.authkit.app`.
- ℹ️ No hace falta configurar redirect URIs a mano: con DCR cada cliente registra las suyas.

### 10.3 Render — servicio arriba y con OAuth
- ⬜ **Resume** el servicio + sube a plan **Starter** (always-on; el free hiberna y tumba el launch).
- ⬜ Setea env (`sync:false`):
  - `AUTHKIT_DOMAIN` = el domain del 10.2
  - `BASE_URL` = **`https://sat69.watr.mx`** ← host de marca (ya en `render.yaml`), NO onrender
  - `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN` = del 10.1
  - (opcional) `SAT69_FREE_DAILY_LIMIT` (default 1) · `SAT69_PAID_USER_IDS` = subs de AuthKit de clientes de paga (ilimitados)
- ⬜ Redeploy. Espera "Live".

### 10.4 Cloudflare Worker — el dominio
- 🟦 `cd landing && wrangler login && wrangler deploy` (el token DNS del zshrc no basta; requiere login).
- ℹ️ El Worker (`landing/src/worker.js`) corre primero y proxya `/connect` + `/.well-known/oauth*` a Render; la landing se sirve en el resto.

### 10.5 Verificación (contra el dominio de marca)
```bash
BASE=https://sat69.watr.mx
curl -sS -o /dev/null -w "%{http_code}\n" $BASE/            # 200 (landing)
curl -sSI $BASE/connect | grep -i www-authenticate          # Bearer resource_metadata="…/oauth-protected-resource/connect"
curl -sS $BASE/.well-known/oauth-protected-resource         # 200 JSON, "resource":"https://sat69.watr.mx/connect"
```
- 🔎 Cliente MCP apuntando a `$BASE/connect` **sin header** → dispara flujo OAuth (login AuthKit), no 401 pelón.
- 🔎 Gate freemium: 2ª consulta del día de un usuario free → dict `{"error":"limite_gratis_alcanzado", …}`.

### 10.6 Rotar bearer + publicar
- 🟦 **Rota `MCP_API_KEY`** (con OAuth sólo protege `/refresh`,`/reload`): regénéralo, actualiza Render + GH Secret.
- 🟦 `cd .. && mcp-publisher login github && mcp-publisher publish` → SAT69 en el registro MCP oficial.

> Modo privado/bearer (Fase 6) sigue válido para clientes B2B que prefieran header estático contra `onrender` — pero el **listado público** exige OAuth (10.2–10.3): sin él, `/connect` da 401 a quien no tenga el bearer.

---

## Resumen de variables

| Variable | Dónde | Requerida |
|----------|-------|-----------|
| `MCP_API_KEY` | Render + GH Secret | Sí (bearer M2M) |
| `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN` | Render | Sí (persistencia durable) |
| `SAT_URL_69B_BIS` | Render + GH Secret | No (vacío = se omite el 69-B Bis) |
| `BASE_URL` | Render | Sí (para OAuth / metadata) |
| `AUTHKIT_DOMAIN` | Render | Sólo si activas OAuth |
| `FETCH_PROXY` / `DATAIMPULSE_*` | Render | No (SAT no bloquea datacenter) |
| `RENDER_BASE_URL` | GH Secret | Sí (cron) |
