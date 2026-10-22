# ESTADO — sat69-mcp

> Medido el 2026-10-08 contra disco, GitHub y la API de Render. Los dos pendientes
> de "Qué lo bloquea" se midieron el 2026-10-22.
> **Lo de abajo caduca.** Si esta fecha ya está vieja, no te creas el contenido:
> vuelve a medirlo con los comandos del final.

## Qué es

Servidor MCP sobre las listas públicas del SAT: artículo 69-B (EFOS,
operaciones simuladas), 69-B Bis (transmisión indebida de pérdidas) y 69
(situación fiscal firme). Un RFC entra, sale un veredicto de riesgo.

**Qué no es:** no es asesoría ni producto final. Es infraestructura de datos:
una pregunta, una respuesta, para que otro la consuma.

## Para quién

Compras, alta de proveedores, due diligence, cuentas por pagar — y agentes.
Hoy lo consume `sat-defensa` para acotar la rama según la situación del RFC.

## Madurez, quién paga y de qué depende

> Escala común a los siete proyectos, para que se puedan comparar:
> **idea** (spec sin código) · **cimiento** (corre, no le sirve a nadie aún) ·
> **usable** (alguien podría usarlo hoy) · **en uso** (alguien lo usa de verdad) ·
> **terminado** (hace lo que prometió; no crece salvo que el uso lo pida).

> Papeles del portafolio: **oráculo** · **componente ligero** · **plataforma pesada** · **integrador/generador**. Definidos en
> `gbrain/brain/02-watr-and-company/strategy/regtech-and-fiscal-defense-portfolio.md`.

| | |
|---|---|
| **Papel** | **Componente ligero.** Una pregunta, una respuesta, integrable por cualquiera — o por Ed cuando no hace falta todo el peso de CentinelIA. |
| **Madurez** | **terminado** y **en uso**. Cero pendientes en su PLAN, a propósito. |
| **Quién paga** | **Nadie, y nadie se ha registrado — pero nunca se puso enfrente de nadie.** Confirmado con Ed el 2026-10-08: *no se ha lanzado nada al público*. En agosto se **construyeron** los activos de lanzamiento —blog, WhatsApp B2B, lista de espera, freemium, suscripción— y ahí se quedaron. El cero no es señal de mercado: es ausencia de intento. Nadie ha dicho que no, porque a nadie se le ha preguntado. |
| **De qué depende** | Los archivos públicos del SAT. Turso para persistir. |
| **Qué depende de él** | **sat-defensa** lo consulta para acotar la rama. **repse** se cruza con él desde el 11-ago (su Fase 3). |

## Dónde vive

- Repo: `edbror/sat69-mcp` · local: `~/mcp-servers/sat69-mcp`
- Servicio: `sat69-mcp` en Render, plan starter, en el cron de hibernación
  (duerme 22:00–05:30)

## En qué estado está

Funcionando y en uso. FastMCP con stdio + Streamable HTTP, OAuth 2.1 (WorkOS
AuthKit) con fallback a bearer estático, persistencia en Turso, despliegue en
Render con cron de refresco por GitHub Actions. Seis archivos de prueba.

Dato que confunde y está documentado: la lista del **69-B Bis tiene tres
registros en todo el país**. Un conteo de un dígito es correcto, no una
importación fallida.

## Qué lo bloquea

Nada para lo que hace hoy. Quedan dos cosas medidas el 22-oct-2026 que valen
anotarse, ninguna de las dos bloqueante.

**1. El servicio sirve el corte del ciclo anterior, y necesita un secret.** El
servicio hace su único pull de Turso al arrancar, a las 11:30 UTC cuando lo
despierta el cron de Render. El scheduler de GitHub Actions llega tarde por horas
(las últimas diez corridas agendadas para las 11:30 arrancaron entre las 14:18 y
las 18:25 UTC), así que la ingesta casi siempre corre DESPUÉS de ese pull y lo que
baja del SAT no llega al índice servido hasta el día siguiente.

Lo cerraría que la ingesta avise al servicio con `POST /reload`, que ya existe y
ya hace el pull. Hace falta el secret **`MCP_API_KEY`** en este repo, con el mismo
valor que la env var del servicio en Render; hoy sólo están los dos de Turso y el
de Cloudflare. Mientras no esté, no se agrega la llamada: un paso que falla en
silencio por un secret vacío es el trap que ya pagamos con `SAT_URL_69B_BIS`.

**2. `push_to_turso` no es atómico.** Hace `DELETE` y luego `INSERT` por lotes de
2,000, así que un `pull` que caiga en medio se trae un pedazo. Está contenido y no
silencioso: el pull levanta `PullFallido` si se cae, y `listas_incompletas()` hace
que el veredicto conteste `NO_VERIFICADO` en vez de un `LIMPIO` falso cuando el
índice está a medias. Lo que falta para cerrarlo de raíz es que el pull escriba en
una base de paso y haga swap al terminar, para que una lectura a medias deje el
índice anterior completo en lugar de uno parcial.

El historial de por qué esto importa: el 18-oct el servicio importó 15,024 de
557,094 filas y pasó el día contestando con el 2.7% de la lista del Art. 69, sin
que nada avisara, porque `pull_from_turso` devolvía el total parcial en vez de
levantar.

## Cómo volver a medir esto

```bash
pytest -q
curl -s https://sat69-mcp.onrender.com/health    # dormido de 22:00 a 05:30
```
