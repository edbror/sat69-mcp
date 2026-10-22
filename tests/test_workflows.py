"""Los workflows parsean, y la ingesta no choca con el despertador.

Existe porque `ruff` y `pytest` no miran `.github/`, así que un YAML roto o un
horario movido pasaban el CI en verde. Ya pasó en el repo de MATERIA: una línea
mal indentada cerró un bloque `run: |` y nada lo detectó.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml  # dev-only; va en pyproject para que esto NO se salte en CI

_WORKFLOWS = sorted((Path(__file__).parent.parent / ".github" / "workflows").glob("*.yml"))

# El cron de Render `mcps-wake-530am` (repo wearetherobots/watr-cloud-ops) despierta
# los MCP a esta hora UTC, y al arrancar el servicio hace su pull de Turso.
_DESPERTADOR_UTC_MIN = 11 * 60 + 30

# Margen mínimo entre que termina la ingesta y arranca el servicio. La corrida más
# lenta medida son 6m37s; 30 minutos deja aire de sobra.
_MARGEN_MIN = 30


def test_hay_workflows_que_revisar():
    assert _WORKFLOWS, "no se encontró ningún workflow; ¿cambió la ruta?"


@pytest.mark.parametrize("ruta", _WORKFLOWS, ids=lambda p: p.name)
def test_el_workflow_parsea(ruta: Path):
    contenido = yaml.safe_load(ruta.read_text())
    assert isinstance(contenido, dict), f"{ruta.name} no parseó a un mapa"
    # `on` es la palabra reservada de YAML 1.1 para True, así que PyYAML la lee
    # como la llave booleana. Las dos formas cuentan.
    assert ("on" in contenido) or (True in contenido), f"{ruta.name} no declara disparador"
    assert contenido.get("jobs"), f"{ruta.name} no declara jobs"


def _schedules_utc(ruta: Path) -> list[int]:
    """Los horarios `cron` del workflow, en minutos desde medianoche UTC."""
    d = yaml.safe_load(ruta.read_text())
    disparador = d.get("on", d.get(True)) or {}
    out = []
    for entrada in (disparador.get("schedule") or []):
        minuto, hora = entrada["cron"].split()[:2]
        if minuto.isdigit() and hora.isdigit():
            out.append(int(hora) * 60 + int(minuto))
    return out


def test_la_ingesta_no_choca_con_el_despertador_del_servicio():
    """`push_to_turso` hace DELETE y luego INSERT por lotes, o sea que no es atómico.
    Si corre mientras el servicio arranca y hace su pull, el servicio se trae un
    pedazo y lo sirve como completo.

    No es un accidente raro: estuvieron agendados al mismo minuto y ya cayó mal. El
    18-oct el servicio importó 15,024 de 557,094 filas y pasó el día contestando
    con el 2.7% de la lista del 69.
    """
    refresh = Path(__file__).parent.parent / ".github" / "workflows" / "refresh.yml"
    horarios = _schedules_utc(refresh)
    assert horarios, "refresh.yml perdió su schedule"

    for t in horarios:
        falta = (_DESPERTADOR_UTC_MIN - t) % (24 * 60)
        assert falta >= _MARGEN_MIN, (
            f"la ingesta corre {falta} min antes de que el servicio despierte "
            f"({_DESPERTADOR_UTC_MIN // 60:02d}:{_DESPERTADOR_UTC_MIN % 60:02d} UTC). "
            "El push no es atómico: el servicio se traería un pull parcial. "
            "Si moviste el despertador, mueve este horario y _DESPERTADOR_UTC_MIN."
        )
