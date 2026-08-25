#!/usr/bin/env python3
"""Watcher del Art. 49 Bis del CFF (nueva "lista negra" por CFDI presuntamente falsos).

A ago-2026 el SAT NO publica el 49 Bis como datos abiertos (archivo/CSV): son
oficios sueltos en el DOF, sin esquema estable. Este watcher revisa —una vez por
semana en CI— si ya apareció un archivo descargable de 49 Bis en las rutas de
datos abiertos del SAT/Azure. Si lo encuentra, imprime `FOUND <url>` (y el
workflow abre un issue). Si no, `NOT_FOUND`.

# ponytail: heurística por URL directa — 0 falsos positivos, pero NO detecta el
# caso "solo SPA / solo DOF". Si el SAT publica detrás del portal dinámico, hay
# que confirmarlo a mano. Upgrade: parsear la consulta del portal o el DOF.
"""
from __future__ import annotations

import sys
import urllib.request

# Rutas candidatas, calcadas del patrón real del 69-B Bis, que vive en el blob
# de Azure bajo Documents_AGGC (el 69-B main está en Documents_AGAFF). El 49 Bis,
# cuando el SAT lo consolide, muy probablemente saldrá en AGGC con el mismo patrón
# `Listado_49_Bis_Completo.csv`. Se prueban ambos contenedores + omawww por si acaso.
_AZURE = "https://wu1agsprosta001.blob.core.windows.net/agsc-publicaciones/Datos_abiertos"
_HOSTS = [
    f"{_AZURE}/Documents_AGGC",   # ← donde vive el 69-B Bis; el candidato más fuerte
    f"{_AZURE}/Documents_AGAFF",
    "http://omawww.sat.gob.mx/cifras_sat/Documents",
]
_NAMES = [
    "Listado_49_Bis_Completo.csv", "Listado_49_Bis_Completo.xls",
    "Listado_completo_49-Bis.csv", "Listado_completo_49-Bis.xls",
    "Listado_49_BIS_Completo.csv", "Listado_completo_Art_49-Bis.csv",
]
_CANDIDATES = [f"{host}/{n}" for host in _HOSTS for n in _NAMES]


def _exists(url: str) -> bool:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "SAT69-watch49bis/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:  # noqa: S310 (URLs fijas de arriba)
            return r.status == 200
    except Exception:
        return False


def main() -> int:
    for url in _CANDIDATES:
        if _exists(url):
            print(f"FOUND {url}")
            return 0
    print("NOT_FOUND")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
