"""
Sincronización Turso (libSQL cloud) ↔ SQLite local.

Estrategia (igual que el MCP de movilizaciones):
  • El SQLite local (efímero en Render) atiende todas las consultas + FTS5.
  • Turso es el almacén durable que sobrevive a los redeploys.
  • Al arrancar → pull_from_turso(): Turso → local (los triggers reconstruyen el FTS).
  • Tras importar → push_to_turso(): local → Turso.

Supuesto: un único escritor (la instancia de Render / el cron). Configurar
TURSO_DATABASE_URL + TURSO_AUTH_TOKEN habilita el sync; si faltan, todo es no-op.
"""
from __future__ import annotations

import logging

from .config import settings

logger = logging.getLogger(__name__)

_TABLES = {
    "registros_69": [
        "id", "rfc", "razon_social", "tipo_persona", "supuesto",
        "fecha_primera_publicacion", "entidad_federativa", "source_file", "imported_at",
    ],
    "registros_69b": [
        "id", "rfc", "nombre", "situacion", "oficio_presuncion_sat",
        "publicacion_sat_presuntos", "publicacion_dof_presuntos",
        "publicacion_sat_desvirtuados", "publicacion_dof_desvirtuados",
        "oficio_definitivos_sat", "publicacion_sat_definitivos",
        "publicacion_dof_definitivos", "publicacion_sat_sentencia_favorable",
        "publicacion_dof_sentencia_favorable", "datos", "imported_at",
    ],
    "registros_69b_bis": [
        "id", "rfc", "nombre", "situacion",
        "oficio_definitivo_sat", "publicacion_sat_definitivo",
        "oficio_definitivo_dof", "publicacion_dof_definitivo",
        "oficio_sentencia_favorable_sat", "publicacion_sat_sentencia_favorable",
        "oficio_sentencia_favorable_dof", "publicacion_dof_sentencia_favorable",
        "datos", "imported_at",
    ],
    "registros_csd": [
        "id", "rfc", "nombre", "supuesto", "fecha_cancelacion",
        "admon_responsable", "fecha_publicacion", "datos", "imported_at",
    ],
    "source_files": [
        "id", "dataset", "source_file", "sha256", "rows",
        "sat_actualizado_al", "status", "error_msg", "fetched_at",
    ],
}

# Las columnas de cada tabla del remoto, con el nombre FUERA de la definicion.
#
# Antes esto era una lista de `CREATE TABLE IF NOT EXISTS <nombre> (...)` con el
# nombre horneado, y bastaba mientras solo hiciera falta crear las tablas reales.
# El push atomico necesita crear las MISMAS tablas con otro nombre (las de paso),
# asi que la definicion tiene que poder recibir el nombre. Con una sola fuente de
# verdad no hay forma de que las dos se separen, que es el defecto clasico de
# tener el esquema escrito dos veces.
_COLUMNAS_DDL = {
    "registros_69": """id INTEGER PRIMARY KEY, rfc TEXT, razon_social TEXT,
        tipo_persona TEXT, supuesto TEXT, fecha_primera_publicacion TEXT,
        entidad_federativa TEXT, source_file TEXT, imported_at TEXT""",
    "registros_69b": """id INTEGER PRIMARY KEY, rfc TEXT, nombre TEXT, situacion TEXT,
        oficio_presuncion_sat TEXT, publicacion_sat_presuntos TEXT,
        publicacion_dof_presuntos TEXT, publicacion_sat_desvirtuados TEXT,
        publicacion_dof_desvirtuados TEXT, oficio_definitivos_sat TEXT,
        publicacion_sat_definitivos TEXT, publicacion_dof_definitivos TEXT,
        publicacion_sat_sentencia_favorable TEXT,
        publicacion_dof_sentencia_favorable TEXT, datos TEXT, imported_at TEXT""",
    "registros_69b_bis": """id INTEGER PRIMARY KEY, rfc TEXT, nombre TEXT, situacion TEXT,
        oficio_definitivo_sat TEXT, publicacion_sat_definitivo TEXT,
        oficio_definitivo_dof TEXT, publicacion_dof_definitivo TEXT,
        oficio_sentencia_favorable_sat TEXT, publicacion_sat_sentencia_favorable TEXT,
        oficio_sentencia_favorable_dof TEXT, publicacion_dof_sentencia_favorable TEXT,
        datos TEXT, imported_at TEXT""",
    "registros_csd": """id INTEGER PRIMARY KEY, rfc TEXT, nombre TEXT, supuesto TEXT,
        fecha_cancelacion TEXT, admon_responsable TEXT, fecha_publicacion TEXT,
        datos TEXT, imported_at TEXT""",
    # El UNIQUE importa y sobrevive al `RENAME`: se comprobo contra el cliente de
    # libsql antes de escribir esto, porque un swap que se lo coma dejaria entrar
    # el mismo archivo dos veces y `listas_incompletas()` compara contra esta fila.
    "source_files": """id INTEGER PRIMARY KEY, dataset TEXT, source_file TEXT UNIQUE,
        sha256 TEXT, rows INTEGER, sat_actualizado_al TEXT, status TEXT,
        error_msg TEXT, fetched_at TEXT""",
}

# El sufijo de las tablas de paso del push. Doble guion bajo para que no choque
# con ningun nombre real ni ahora ni despues.
_SUFIJO_PASO = "__nuevo"


def _crear(tabla: str, *, como: str | None = None) -> str:
    """El `CREATE TABLE IF NOT EXISTS` de `tabla`, opcionalmente con otro nombre."""
    return f"CREATE TABLE IF NOT EXISTS {como or tabla} ({_COLUMNAS_DDL[tabla]})"


# Se conserva el nombre de antes porque lo usan el pull y la fixture de las
# pruebas; ahora se deriva, asi que no puede desincronizarse de `_COLUMNAS_DDL`.
_SCHEMA_STMTS = [_crear(t) for t in _COLUMNAS_DDL]


class PullFallido(RuntimeError):
    """El pull desde Turso no terminó. El local quedó a medias."""


_PUSH_CHUNK = 2000
_PULL_CHUNK = 5000  # ponytail: pull por lotes; 512Mi de Render no aguanta ½M dicts de golpe


def _normalize_url(url: str) -> str:
    """La URL de Turso, forzada a **HTTP**. No lo cambies sin leer esto.

    Esta instancia sólo habla HTTP. Está medido las dos veces, el 2026-11-18:

        https://  →  funciona, pero `client.transaction()` da
                     TRANSACTIONS_NOT_SUPPORTED: The HTTP client does not
                     support transactions.
        wss://    →  400, message='Invalid response status'

    O sea que el consejo del propio mensaje de error —"use a libsql:, ws: or
    wss: URL"— **no aplica a este endpoint**, y la normalización a `https://`
    que traía el commit inicial del repo no era arbitraria: era necesaria y le
    faltaba el comentario. Yo la cambié a `wss://` creyendo que era un descuido
    y la corrida reprobó en el primer pull.

    Consecuencia de diseño: **el push no puede usar `transaction()`.** Lo que sí
    hay es `batch()`, que el repo ya usaba para los INSERT, así que el swap va
    en un batch. Ver `push_to_turso`.
    """
    for prefijo in ("libsql://", "wss://", "ws://"):
        if url.startswith(prefijo):
            return "https://" + url[len(prefijo):]
    return url


def _client():
    if not settings.turso_enabled:
        return None
    try:
        import libsql_client
        return libsql_client.create_client_sync(
            url=_normalize_url(settings.turso_url), auth_token=settings.turso_token
        )
    except ImportError:
        logger.error("libsql-client no instalado — pip install libsql-client")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.error("No se pudo conectar a Turso: %s", exc)
        return None


def pull_from_turso() -> int:
    """Turso → local (reemplazo completo, **en UNA transacción**). Filas importadas.

    Lee cada tabla remota en páginas de `_PULL_CHUNK` e inserta sobre la marcha,
    para no materializar ½M de filas en memoria (el free tier de Render son
    512Mi). Lo que cambió es *dónde* están los commits.

    ## Por qué una sola transacción, y qué costaba no tenerla

    Antes esto abría una conexión nueva —y por lo tanto hacía un commit— por cada
    `DELETE` y por cada lote de 5,000. O sea que el índice que el servicio estaba
    sirviendo se reconstruía **en vivo**, y eso tenía dos consecuencias, las dos
    medidas el 2026-11-16 contra una base sembrada con los volúmenes reales
    (508,264 filas del 69 y 60,582 del CSD, 168 MB):

    1. **Lecturas parciales mientras corre.** Un lector concurrente vio el índice
       a medias en **32 de 44** muestras, incluido cero. Con una transacción:
       **0 de 106**; vio las 508,264 filas todo el tiempo y el cambio apareció de
       golpe. Esto se había visto en producción el 12-nov: el servicio subió de
       165 mil a 508 mil filas con `69b` en cero durante unos dos minutos.
    2. **Y lo que queda si se cae a la mitad.** Con commit por lote, una caída en
       el lote 40 dejaba **39.3% de la lista** instalada como si fuera completa.
       Con una transacción, el `rollback` deja el índice anterior **intacto**.
       Eso es exactamente el incidente del 18-oct, cuando el servicio importó
       15,024 de 557,094 filas y pasó el día contestando con el 2.7% de la lista.

    Funciona porque `get_conn()` pone `PRAGMA journal_mode=WAL`: en WAL los
    lectores no se bloquean y ven el último estado **comprometido**, así que
    mientras esta transacción escribe, el servicio sigue respondiendo con el
    índice anterior completo. Y `get_conn()` ya hace commit al salir bien y
    rollback al salir con excepción, que es justo la semántica que hace falta;
    lo único que hacía falta era subir el `with` afuera de los bucles.

    ## Lo que cuesta, dicho con el número

    Tarda **2.4x más** (23.1s → 56.1s medidos) y el WAL llega a 177 MB en vez de
    129, porque una transacción grande no puede ir liberando páginas. Para algo
    que corre una vez al día y cuyo precio es dejar de servir datos a medias, el
    cambio sobra. El `wal_checkpoint` de abajo devuelve el espacio al terminar.

    ## Lo que esto NO arregla

    El `push_to_turso` sigue sin ser atómico del lado remoto, así que un pull que
    caiga en medio de un push se trae un snapshot parcial y lo instala **completo
    y consistente**, sólo que incompleto. Sigue contenido y no silencioso:
    `listas_incompletas()` compara contra `source_files.rows` y hace que el
    veredicto conteste `NO_VERIFICADO` en vez de un `LIMPIO` falso. Y desde el
    #28 la carrera casi no existe, porque el `/reload` lo dispara la ingesta
    *después* de que su push terminó. Cerrarlo de raíz pide tablas de paso
    remotas y un swap de nombres: `TransactionSync` de libsql sólo expone
    `execute`, no `batch`, así que una transacción remota serían 584 mil viajes
    HTTP en vez de 292 lotes.
    """
    client = _client()
    if client is None:
        logger.info("Turso no configurado — se omite pull")
        return 0

    from . import database as db

    total = 0
    try:
        for stmt in _SCHEMA_STMTS:
            client.execute(stmt)

        # **Una sola transacción para TODO el reemplazo.** Si algo de aquí
        # adentro truena, `get_conn()` hace rollback y el índice anterior queda
        # entero. Las lecturas remotas van dentro a propósito: tener la
        # transacción local abierta mientras se pagina Turso no estorba a nadie,
        # porque en WAL los lectores no se bloquean.
        with db.get_conn() as conn:
            for table, cols in _TABLES.items():
                n = client.execute(f"SELECT count(*) FROM {table}").rows[0][0]
                conn.execute(f"DELETE FROM {table}")
                if not n:
                    continue
                col_list = ", ".join(cols)
                insert = (
                    f"INSERT INTO {table} ({col_list}) "
                    f"VALUES ({', '.join('?' for _ in cols)})"
                )
                for offset in range(0, n, _PULL_CHUNK):
                    res = client.execute(
                        f"SELECT {col_list} FROM {table} LIMIT {_PULL_CHUNK} OFFSET {offset}"
                    )
                    conn.executemany(insert, [list(row) for row in res.rows])
                    total += len(res.rows)
    except Exception as exc:
        # NO se devuelve el total parcial. Devolverlo hacía que una falla a medio
        # pull fuera indistinguible de un pull chico y exitoso, y eso es justo lo
        # que pasó el 18-oct: el log dijo "Importadas 15,024 filas" y el servicio
        # sirvió el 2.7% de la lista todo el día. Quien llame decide si tolera la
        # falla; lo que no puede es no enterarse.
        logger.error("pull_from_turso falló tras %d filas: %s", total, exc)
        raise PullFallido(f"pull incompleto tras {total} filas: {exc}") from exc
    finally:
        client.close()

    if total == 0:
        logger.info("Turso vacío — nada que importar")
    else:
        logger.info("Importadas %d filas de Turso", total)

    # Una transacción de medio millón de filas deja el WAL en ~177 MB (medido), y
    # sin esto se queda así hasta que SQLite decida reciclarlo solo. En el disco
    # efímero de Render eso es espacio que nadie va a reclamar. El checkpoint no
    # puede fallar de forma interesante, pero si falla no vale tirar un pull que
    # ya quedó bien: es higiene, no corrección.
    try:
        with db.get_conn() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as exc:  # noqa: BLE001
        logger.warning("no se pudo truncar el WAL tras el pull: %s", exc)

    return total


def push_to_turso() -> int:
    """Local → Turso (reemplazo completo). Devuelve total enviado, 0 si falló.

    ## Por qué tablas de paso y un swap, y no `DELETE` + `INSERT`

    Antes esto hacía, por tabla, un `DELETE FROM <tabla>` y luego `INSERT` por
    lotes **sobre la tabla que el servicio lee**. Durante los ~3 minutos del push
    el remoto pasaba por estar vacío y luego a medio llenar, y si se caía a la
    mitad el almacén durable se quedaba así hasta la corrida siguiente. Medido
    con el código real, cortando en el segundo lote: el remoto quedaba con 2,000
    filas y CERO de las 1,500 que tenía.

    Ahora los `INSERT` van a tablas de paso (`<tabla>__nuevo`), que nadie lee, y
    al final el swap cambia los nombres de las cinco. Un lector ve o todo lo
    viejo o todo lo nuevo. Si algo truena antes del swap, el remoto queda
    exactamente como estaba y lo único que sobra son las tablas de paso, que la
    corrida siguiente tira con su `DROP TABLE IF EXISTS`.

    ## Por qué el swap va en `batch` y NO en `transaction`

    Porque sobre este endpoint `transaction()` no existe. Las dos mediciones del
    2026-11-18, en ese orden:

        https://  →  TRANSACTIONS_NOT_SUPPORTED: The HTTP client does not
                     support transactions. Please use a libsql:, ws: or wss: URL.
        wss://    →  400, message='Invalid response status'

    O sea que el consejo del propio mensaje de error no aplica aquí, y la
    normalización a `https://` que trae `_normalize_url` es necesaria. Lo que sí
    hay sobre HTTP es `batch`, que este archivo ya usaba para los INSERT, y el
    servidor lo ejecuta como una unidad: se comprobó que un batch con una
    sentencia que revienta a la mitad no deja ninguna de las otras.

    Los `INSERT` se quedan en batches aparte y NO dentro del batch del swap: son
    584 mil filas en 292 lotes, y meterlas en una sola unidad no tendría por qué
    caber. El batch del swap son diez sentencias de DDL.

    ## Lo que sigue sin cubrir

    La lectura del local que alimenta esto no toma un snapshot: `data` se arma
    con un `SELECT *` por tabla dentro de una sola conexión, así que si alguien
    escribiera el local a la mitad de esa lectura, el push mandaría tablas de
    distintos momentos. Hoy no pasa porque el único escritor es la propia
    ingesta, que es quien llama a esto y lo hace después de terminar de
    importar. Queda anotado porque el día que haya dos escritores, deja de ser
    cierto.
    """
    client = _client()
    if client is None:
        logger.info("Turso no configurado — se omite push")
        return 0

    from libsql_client import Statement

    from . import database as db
    with db.get_conn() as conn:
        data = {
            t: [dict(r) for r in conn.execute(f"SELECT * FROM {t}").fetchall()]
            for t in _TABLES
        }

    de_paso = {t: f"{t}{_SUFIJO_PASO}" for t in _TABLES}

    try:
        for stmt in _SCHEMA_STMTS:
            client.execute(stmt)

        # Las de paso se tiran y se recrean. El `DROP` de aquí es lo que limpia
        # lo que haya dejado una corrida que se cayó antes del swap.
        for tabla, paso in de_paso.items():
            client.execute(f"DROP TABLE IF EXISTS {paso}")
            client.execute(_crear(tabla, como=paso))

        total = 0
        for tabla, cols in _TABLES.items():
            paso = de_paso[tabla]
            rows = data[tabla]
            placeholders = ", ".join("?" for _ in cols)
            insert = f"INSERT INTO {paso} ({', '.join(cols)}) VALUES ({placeholders})"
            for i in range(0, len(rows), _PUSH_CHUNK):
                batch = [
                    Statement(insert, [r.get(c) for c in cols])
                    for r in rows[i:i + _PUSH_CHUNK]
                ]
                if batch:
                    client.batch(batch)
                    total += len(batch)

        # **El swap. Hasta aquí el remoto no se tocó.**
        #
        # Va en UN `batch` y no en `client.transaction()`, y eso no es
        # preferencia: este endpoint sólo habla HTTP y el transporte HTTP de
        # libsql no tiene transacciones (ver `_normalize_url`, con las dos
        # mediciones). `batch` sí existe sobre HTTP —el repo ya lo usaba para
        # los INSERT— y el servidor lo ejecuta como una unidad: comprobado que
        # un batch con una sentencia que revienta a la mitad no deja ninguna de
        # las otras.
        #
        # Diez sentencias: por tabla, tirar la real y renombrar la de paso.
        client.batch([
            Statement(sql)
            for tabla, paso in de_paso.items()
            for sql in (
                f"DROP TABLE IF EXISTS {tabla}",
                f"ALTER TABLE {paso} RENAME TO {tabla}",
            )
        ])

        logger.info("Enviadas %d filas a Turso", total)
        return total
    except Exception as exc:  # noqa: BLE001
        # Se devuelve 0 y no se levanta, como antes: quien llama (el pipeline)
        # reporta "(push Turso: 0)" y el paso del workflow que avisa al servicio
        # se omite, que es lo correcto porque no hay nada nuevo que recargar.
        logger.error("push_to_turso falló: %s", exc)
        # Las de paso no se borran aquí a propósito: si el fallo fue de red, otro
        # intento de borrar falla igual y taparía el error de arriba. La corrida
        # siguiente las tira con su `DROP TABLE IF EXISTS`.
        return 0
    finally:
        client.close()
