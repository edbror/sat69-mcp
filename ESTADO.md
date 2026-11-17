# ESTADO — sat69-mcp

> Medido el 2026-10-08 contra disco, GitHub y la API de Render. Los dos pendientes
> de "Qué lo bloquea" se cerraron el 2026-11-16 y se volvieron a medir ese día,
> contra el servicio vivo y contra una base con los volúmenes reales.
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
Render con cron de refresco por GitHub Actions. Nueve archivos de prueba.

Dato que confunde y está documentado: la lista del **69-B Bis tiene tres
registros en todo el país**. Un conteo de un dígito es correcto, no una
importación fallida.

## Qué lo bloquea

Nada. Las dos cosas que estaban anotadas aquí se cerraron el 16-nov-2026 y se
quedan escritas con su medición, porque el por qué de cada decisión es lo que
evita que alguien la deshaga. De la segunda queda un residuo, contenido y no
silencioso, con el motivo de no cerrarlo todavía.

**1. El servicio servía el corte del ciclo anterior.**

*Cerrado el 2026-11-16* con los PR #27 y #28. El servicio hacía su único pull de
Turso al arrancar, a las 11:30 UTC cuando lo despierta el cron de Render, y la
ingesta corre alrededor de las 18:00, así que lo que bajaba del SAT no llegaba al
índice servido hasta el día siguiente. El 12-nov se midió sirviendo el corte del
4-nov con ocho corridas verdes de por medio.

Ahora la ingesta llama a `POST /reload` al terminar. El secret `MCP_API_KEY` ya
está en este repo, con el mismo valor que la env var del servicio en Render.

Dos cosas que el paso cuida y vale no deshacer. El secret **se exige**: si viene
vacío el workflow truena, porque un paso que falla en silencio por un secret
vacío es el trap que ya pagamos con `SAT_URL_69B_BIS`. Y el reload **sólo corre
si la ingesta empujó algo**: recargar sin novedades hacía al servicio re-bajar
las ~583 mil filas para nada, y el SAT publica cada varias semanas, así que el
caso normal es que no haya cambios.

**2. El pull ya es atómico; al push le queda un residuo contenido.**

*Cerrado el 2026-11-16.* `pull_from_turso` abría una conexión —y por lo tanto
hacía un commit— por cada `DELETE` y por cada lote de 5,000, o sea que
reconstruía **en vivo** el índice que el servicio estaba sirviendo. Ahora todo el
reemplazo va en UNA transacción. Funciona porque `get_conn()` pone
`journal_mode=WAL`: los lectores no se bloquean y ven el último estado
comprometido, así que mientras el pull escribe, el servicio contesta con el
índice anterior completo.

Medido contra una base sembrada con los volúmenes reales (508,264 filas del 69 y
60,582 del CSD, 168 MB), comparando las dos formas:

| | commit por lote | una transacción |
|---|---|---|
| lecturas parciales de un lector concurrente | **32 de 44** | **0 de 106** |
| lo que deja una caída en el lote 40 | 39.3% de la lista | índice anterior intacto |
| duración | 23.1 s | 56.1 s |
| WAL máximo | 129 MB | 177 MB |

Cuesta 2.4x más y el WAL crece 48 MB; para algo que corre una vez al día y cuyo
precio era servir datos a medias, sobra. Un `wal_checkpoint(TRUNCATE)` al
terminar devuelve el espacio.

Las dos filas de arriba son los dos tests nuevos de
`test_pull_antes_de_importar.py`, y los dos reprueban con el código viejo: el
espía del segundo registró `[12, 12, 12, 0, 5, 10, ...]`, o sea que un lector
alcanzaba a ver **cero** filas.

*Lo que queda.* `push_to_turso` sigue haciendo `DELETE` y luego `INSERT` por
lotes de 2,000 contra el remoto, así que un pull que caiga en medio de un push se
trae un snapshot parcial. Ahora lo instala completo y consistente, sólo que
incompleto, y sigue contenido y no silencioso: `listas_incompletas()` compara
contra `source_files.rows` y el veredicto contesta `NO_VERIFICADO` en vez de un
`LIMPIO` falso. Desde el #28 la carrera casi no existe, porque el `/reload` lo
dispara la ingesta *después* de que su push terminó. Cerrarlo de raíz pide tablas
de paso remotas y un swap de nombres: `TransactionSync` de libsql sólo expone
`execute`, no `batch`, así que una transacción remota serían 584 mil viajes HTTP
en vez de 292 lotes.

El historial de por qué esto importa: el 18-oct el servicio importó 15,024 de
557,094 filas y pasó el día contestando con el 2.7% de la lista del Art. 69, sin
que nada avisara, porque `pull_from_turso` devolvía el total parcial en vez de
levantar. Ese modo de falla ahora tiene su test.

## Cómo volver a medir esto

```bash
pytest -q
curl -s https://sat69-mcp.onrender.com/health    # dormido de 22:00 a 05:30
```
