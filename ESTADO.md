# ESTADO — sat69-mcp

> Medido el 2026-10-08 contra disco, GitHub y la API de Render. Los dos pendientes
> de "Qué lo bloquea" se cerraron entre el 2026-11-16 y el 2026-11-18, y se
> midieron esos días contra el Turso de producción, el servicio vivo y una base
> con los volúmenes reales.
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
Render con cron de refresco por GitHub Actions. Diez archivos de prueba.

Dato que confunde y está documentado: la lista del **69-B Bis tiene tres
registros en todo el país**. Un conteo de un dígito es correcto, no una
importación fallida.

## Qué lo bloquea

Nada. Las tres cosas que estaban anotadas aquí se cerraron entre el 16 y el 18-nov-2026 y
se quedan escritas con su medición, porque el por qué de cada decisión es lo que
evita que alguien la deshaga.

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

**2. El pull y el push ya son atómicos.**

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

*El push también, cerrado el 2026-11-18.* Hacía `DELETE FROM <tabla>` y luego
`INSERT` por lotes **sobre la tabla que el servicio lee**, así que durante los
~3 minutos del push el remoto pasaba por vacío y luego a medio llenar, y una
caída dejaba el almacén durable incompleto hasta la corrida siguiente. Medido
con el código real, cortando en el segundo lote: quedaba con 2,000 filas y
**cero** de las 1,500 que tenía. Ahora los `INSERT` van a tablas de paso
(`<tabla>__nuevo`) y el swap cambia los nombres de las cinco; mismo corte, el
remoto queda con sus 1,500 intactas.

**El swap va en un `batch`, NO en una transacción, y eso costó dos corridas
rojas.** Las mediciones, en orden:

| transporte | qué pasó |
|---|---|
| `https://` + `transaction()` | `TRANSACTIONS_NOT_SUPPORTED: The HTTP client does not support transactions. Please use a libsql:, ws: or wss: URL` |
| `wss://` (siguiendo ese consejo) | `400, message='Invalid response status'` en el primer pull |
| `https://` + `batch()` | **funcionó**: 583,870 filas enviadas y el servicio recargó las cuatro listas completas |

O sea que el consejo del propio mensaje de error **no aplica a este endpoint**, y
la normalización a `https://` del commit inicial era necesaria: le faltaba el
comentario, no el motivo. Lo que sí hay sobre HTTP es `batch`, que ya se usaba
para los 292 lotes de INSERT, y el servidor lo ejecuta como una unidad. Los
INSERT se quedan en batches aparte: 584 mil filas no tienen por qué caber en uno.

Verificado contra el Turso real el 2026-11-18, corriendo el workflow **desde la
rama** para no redesplegar el servicio con código sin probar:

```
Importadas 583870 filas de Turso          <- el pull atómico, sobre HTTP
Enviadas 583870 filas a Turso             <- el push con tablas de paso y swap
Resultado: 583862 registros importados; 0 error(es). (push Turso: 583870)
POST /reload -> 200, 583870 filas, 508264 / 15013 / 3 / 60582
```

Las dos corridas fallidas no tocaron el remoto: el push nuevo no toca las tablas
reales hasta el swap, el arreglo del conteo de abajo las marcó en rojo, y correr
desde la rama dejó al servicio fuera del experimento.

*Y un defecto que esto destapó, del #28.* `turso_rows_pushed` valía 0 en dos
casos muy distintos: cuando no había nada que empujar y cuando el push falló.
Desde que el workflow decide con ese número, un push roto imprimía "el SAT no
publicó nada nuevo" y la corrida pasaba en VERDE **todos los días**, mientras el
almacén durable se quedaba sin lo que la ingesta acababa de importar. Ahora un
push que no envía nada habiendo cambios cuenta como error: `main()` sale con 1 y
el paso del workflow truena antes de llegar al aviso al servicio. Hay test de
los dos lados, porque hacer que todo cero fuera error habría dejado el cron en
rojo los días que el SAT no publica, que son casi todos.

*Lo que sigue sin cubrir.* El swap es atómico, pero la lectura del local que lo
alimenta no toma un snapshot: `data` se arma con un `SELECT *` por tabla en una
sola conexión, así que con dos escritores el push podría mandar tablas de
distintos momentos. Hoy no pasa porque el único escritor es la propia ingesta,
que es quien llama al push y lo hace después de terminar de importar. Queda
anotado porque el día que haya dos, deja de ser cierto.

Y hasta hoy `push_to_turso` **no tenía ni un test**: la única mención en la suite
lo reemplazaba por un lambda, así que las 98 pruebas podían estar en verde con el
push roto. Ahora tiene cinco en `test_push_atomico.py`.

El historial de por qué esto importa: el 18-oct el servicio importó 15,024 de
557,094 filas y pasó el día contestando con el 2.7% de la lista del Art. 69, sin
que nada avisara, porque `pull_from_turso` devolvía el total parcial en vez de
levantar. Ese modo de falla ahora tiene su test.

**3. Una pérdida silenciosa al parsear ya no pasa de largo.**

*Cerrado el 2026-11-18 (WE-1301).* Investigar por qué el Art. 69 bajó 26 filas
destapó que ninguno de los dos candados que había puede ver el peor modo de
falla: que el parser se coma filas. `listas_incompletas()` contrasta la tabla
contra lo declarado, y las dos cifras salen de lo que NOSOTROS parseamos, así que
bajan juntas y siguen cuadrando. Y `MERMA_MAX` es un umbral sobre el total: mil
filas perdidas de 264,766 son 0.38% y pasan de largo. El precio del falso
negativo es el caro: `verificar_rfc` contesta LIMPIO para quien sí está en la
lista.

Los bytes del archivo del SAT son la única cifra de `source_files` que **no**
sale de nuestro parseo, y de ahí su valor: no se mueven cuando el que falla somos
nosotros. Lo que se guarda es `source_files.bytes`, y lo que se compara es la
correspondencia de los deltas: si se perdieron N filas, el archivo tenía que
adelgazar como N filas.

Se descartó la "banda de bytes por fila" que proponía el issue, por medición: una
pérdida de mil filas mueve el promedio 0.38%, así que la banda tendría que ser
tan estrecha que cualquier cambio del SAT la rompería. El delta sí separa los dos
casos con holgura:

| | filas | bytes | esperado | ratio |
|---|---|---|---|---|
| Firmes, baja real del 18-nov | −17 | −1,228 | −1,294 | **0.95** |
| No_localizados, baja real | −7 | −591 | −582 | **1.02** |
| Cancelados, baja real | −2 | −253 | −223 | **1.13** |
| parser que se come filas | −10 a −50,000 | 0 | −761 a −3.8 M | **0.00** |

`CORRESPONDENCIA_MIN = 0.30`: 3x de margen contra el caso real más apretado y 0.30
de distancia al modo de falla, que da 0.00 sin importar la magnitud.

**Lo que NO cubre, dicho como tal:** si el SAT achica las filas Y el parser se
come algunas, los dos efectos se cancelan y esto no lo ve. Protege contra la
pérdida silenciosa a formato constante, que es el caso realista.

Y **no protege en su primera corrida**: los archivos importados antes de que la
columna existiera tienen `bytes` en NULL, no hay línea base, y el guard lo dice
en el log como WARNING —no como info, donde se perdería entre las cien líneas de
descarga del cron— y empieza a vigilar en la corrida siguiente. Esa línea marca
con precisión el día que empezó a proteger.

Catorce pruebas en `tests/test_correspondencia_bytes.py`, y lo que les da valor
es haberlas visto en rojo. Siete sabotajes, cada uno por su propia razón:

| sabotaje | resultado |
|---|---|
| el guard no se llama desde `_verificar_frescura` | rojo: `test_el_guard_esta_cableado_a_la_importacion` |
| `record_source` sin los bytes (el cable cortado) | rojo: el cableado, 2 pruebas |
| el dict de `get_source_state` sin `bytes` | rojo: el cableado, 2 pruebas |
| umbral tan flojo que no atrapa nada | rojo: las 7 que exigen que atrape |
| umbral tan estricto que reprueba al SAT real | rojo: 2 de las 3 bajas reales |
| UNA de las cuatro rutas sin `raw=raw` | rojo: el recorrido del AST |
| UNA de las cuatro rutas sin `bytes_origen` | rojo: el recorrido del AST |

Los dos últimos existen porque `raw` es **opcional** en `_verificar_frescura` —lo
es porque los otros dos candados no lo necesitan y dieciocho pruebas los
ejercitan con tres argumentos—, y una ruta de importación que olvide pasarlo
apaga la correspondencia **sólo para ese archivo**, en silencio y en verde. El
test recorre el AST en vez de grepear: el código dice quién pasa los bytes, no
una línea que partida en dos deja mentir al grep.

**El primer sabotaje salió VERDE y el candado estaba inservible**: los doce tests
que había llamaban a `_verificar_correspondencia` a mano, así que borrar la línea
que lo invoca no rompía ninguno. De ahí salió el test trece, que entra por
`_import_csd` como entra el cron y pierde UNA fila de 300 —0.33%, por debajo de
`MERMA_MAX`—, con lo cual el único candado que puede verlo es éste.

**Y la medición del sabotaje también mintió.** Cambiar `0.30` por `1.10` no
cambia el tamaño del archivo, y Python invalida el `.pyc` por (mtime, tamaño); el
`cp` de restauración cayó en el mismo segundo y el intérprete siguió ejecutando
el bytecode saboteado durante tres corridas "limpias" que salieron rojas. El
arnés ahora borra `__pycache__` entre pasos. Segunda vez en este repo que la
regla aplica: un candado no vale hasta verlo fallar, y la forma de medir el
sabotaje se equivoca igual que el código.

*Del lado de Turso*, la columna entra en `_TABLES` y en `_COLUMNAS_DDL` —si se
agrega al esquema local y no a esas dos listas, el push no la manda y el pull la
deja en NULL en cada arranque, con lo cual el guard nunca tendría línea base y
callaría para siempre—, y el pull hace el `ALTER TABLE` remoto antes de su
`SELECT`, porque `CREATE TABLE IF NOT EXISTS` no migra la tabla que ya está allá
y el pull corre antes que el push. Si ese ALTER falla, **no se diagnostica por el
texto del error**: se comprueba con un `SELECT` de la columna. Del otro lado no
hay SQLite sino libsql por HTTP, las credenciales sólo viven en los secrets del
repo, y suponer la redacción de un error que no se puede ver desde esta máquina
es exactamente el hueco que se colo en el #31.

## Cómo volver a medir esto

```bash
pytest -q
curl -s https://sat69-mcp.onrender.com/health    # dormido de 22:00 a 05:30
```
