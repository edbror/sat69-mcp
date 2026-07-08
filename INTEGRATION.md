# Integración — MCP SAT 69 / 69-B (WATR)

Guía para que un agente de IA (Claude Code, Claude Desktop, Cowork o cualquier
cliente MCP) se conecte y consulte las listas del **SAT** (Servicio de
Administración Tributaria de México):

- **Artículo 69-B del CFF (EFOS)** — contribuyentes con operaciones simuladas.
- **Artículo 69 del CFF** — contribuyentes con situación fiscal firme.

El servidor descarga los CSV oficiales de datos abiertos del SAT, los normaliza
en SQLite (+ FTS5) y expone consultas por RFC, verificación en lote, búsqueda
por nombre y un veredicto de riesgo. Sin OCR: los CSV ya vienen estructurados.

---

## Conexión

| | |
|---|---|
| **Endpoint** | `https://sat69-mcp.onrender.com/mcp` |
| **Transporte** | Streamable HTTP (MCP) |
| **Auth** | `Authorization: Bearer <MCP_API_KEY>` (o OAuth 2.1 si AuthKit está activo) |
| **Health** | `GET https://sat69-mcp.onrender.com/health` (sin auth) |

> 🔐 El Bearer es un secreto: trátalo como credencial (no lo publiques ni lo comitees).
> En plan free el servicio hiberna; el primer request tras inactividad puede
> tardar ~30–60 s (cold start).

### Claude Code (CLI)

```bash
claude mcp add --transport http sat69 \
  https://sat69-mcp.onrender.com/mcp \
  --header "Authorization: Bearer <MCP_API_KEY>"
```

O en `.mcp.json` del proyecto:

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

### Claude Desktop / Cowork

Como conector remoto (Settings → Connectors → Add custom connector) con la misma
URL y header. Si tu versión sólo acepta servidores locales, usa el puente
`mcp-remote`:

```json
{
  "mcpServers": {
    "sat69": {
      "command": "npx",
      "args": [
        "-y", "mcp-remote",
        "https://sat69-mcp.onrender.com/mcp",
        "--header", "Authorization: Bearer <MCP_API_KEY>"
      ]
    }
  }
}
```

### Local (stdio, sin red)

Con el paquete instalado (`pip install -e .`): comando `sat69-mcp` (o
`python -m sat69`). Útil para desarrollo y para Cowork en modo local.

---

## Herramientas (tools)

### `verificar_rfc(rfc)`
Consulta **principal**. Verifica un RFC contra ambas listas y devuelve un
veredicto de riesgo. El RFC se normaliza (mayúsculas, sin espacios/guiones).

Devuelve: `rfc`, `rfc_valido`, `riesgo`, `veredicto`, `en_69`, `en_69b`,
`registros_69[]`, `registros_69b[]`.

### `verificar_lote(rfcs)`
Verifica una lista de hasta **500** RFCs (cartera de proveedores/clientes).
Devuelve sólo los hallazgos, ordenados por severidad, más un `conteo_por_riesgo`.
Ideal para compliance de cuentas por pagar.

### `buscar_nombre(texto, dataset="ambos", limite=25)`
Búsqueda por **nombre / razón social** parcial (FTS5, insensible a acentos)
cuando no se tiene el RFC exacto. `dataset` ∈ `69 | 69b | ambos`.

### `estado_datos()`
Vigencia declarada por el SAT (`sat_actualizado_al`), conteos por situación
(69-B) y por supuesto (69), y fecha de la última importación.

### `actualizar_datos(dataset="all", force_refresh=false)`
Descarga y sincroniza los listados del SAT. Idempotente por hash de archivo
(pasa `force_refresh=true` para forzar). `dataset` ∈ `all | 69 | 69b`.
Normalmente lo dispara el cron; úsalo manualmente si necesitas datos frescos ya.

---

## Niveles de riesgo (campo `riesgo`)

| Riesgo | Origen | Implicación |
|--------|--------|-------------|
| `CRITICO` | 69-B **Definitivo** | Operaciones simuladas confirmadas; los CFDI **no** producen efectos fiscales. |
| `ALTO` | 69-B **Presunto** | Presunción de inexistencia; seguir hasta la resolución definitiva. |
| `MEDIO` | 69 firme / exigible / no localizado | Situación fiscal irregular firme. |
| `BAJO` | 69-B desvirtuado / sentencia favorable | Aclarado; conservar soporte. |
| `INFORMATIVO` | 69 cancelado / condonado | Registro informativo. |
| `LIMPIO` | — | Sin coincidencias a la fecha de los datos. |

El 69-B **manda** sobre el 69: si un RFC aparece en ambos, gana la severidad del 69-B.

---

## Modelo de datos

**Registro 69-B** (`registros_69b[]`): `rfc`, `nombre`, `situacion`
(`Presunto | Desvirtuado | Definitivo | Sentencia Favorable`) y las fechas de
publicación DOF por etapa (presuntos / definitivos / sentencia favorable).

**Registro 69** (`registros_69[]`): `rfc`, `razon_social`, `tipo_persona`
(`F`/`M`), `supuesto` (`FIRMES, CANCELADOS, NO LOCALIZADOS, EXIGIBLES,
SENTENCIAS, CONDONADOS`), `fecha_primera_publicacion`, `entidad_federativa`.

Las tools devuelven objetos JSON. En MCP, el contenido viene como texto JSON
dentro de `result.content[0].text`.

---

## Cómo usarlo (guía para el agente)

- **"¿Este proveedor está en la lista negra del SAT?"** → `verificar_rfc(rfc)`; mira `riesgo` y `veredicto`.
- **"Revisa a todos mis proveedores"** → `verificar_lote(rfcs=[...])`; prioriza `CRITICO`/`ALTO`.
- **"No tengo el RFC, sólo el nombre"** → `buscar_nombre("razón social")`, toma el RFC y confirma con `verificar_rfc`.
- **"¿Qué tan actualizados están los datos?"** → `estado_datos()` (`sat_actualizado_al`, `ultima_importacion`).
- **"Actualiza ya"** → `actualizar_datos()` (o `force_refresh=true`).

**Buenas prácticas:**
- Para due diligence de facturación, un `CRITICO` (EFOS definitivo) significa que
  los CFDI de ese emisor **no** son deducibles/acreditables: escala a fiscal.
- Confirma siempre por RFC; `buscar_nombre` es sólo para localizarlo.
- Los datos reflejan la última importación; para trámites formales valida contra
  el portal oficial del SAT.
- **No** constituye asesoría fiscal ni legal.

---

## Estado actual (referencia)

Fuentes: datos abiertos del SAT — 69-B (`Listado_Completo_69-B.csv`, ~14 k
registros) y 69 (6 archivos, ~½ millón de registros). Persistencia durable en
Turso (sobrevive redeploys). Repo: `github.com/edbror/sat69-mcp`.
