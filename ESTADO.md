# ESTADO — sat69-mcp

> Medido el 2026-10-08 contra disco, GitHub y la API de Render.
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

| | |
|---|---|
| **Madurez** | **terminado** y **en uso**. Cero pendientes en su PLAN, a propósito. |
| **Quién paga** | Nadie directamente. Hubo secuencia de lanzamiento en agosto —blog, WhatsApp B2B, lista de espera, freemium, suscripción— que registra ejecución, no adopción. |
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

Nada. Es de los pocos del portafolio que está terminado para lo que hace.

## Cómo volver a medir esto

```bash
pytest -q
curl -s https://sat69-mcp.onrender.com/health    # dormido de 22:00 a 05:30
```
