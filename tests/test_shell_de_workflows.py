"""El shell embebido en los `run:` de los workflows parsea.

Existe porque `test_workflows.py` valida que el YAML parsee, y eso NO es lo
mismo. El 2026-11-16 un `run:` quedó con una comilla sin cerrar —
`echo "filas empujadas a Turso: ${empujadas:-0}` — y el YAML parseaba
perfectamente: para YAML es una cadena más dentro de un bloque. El workflow se
mergeó verde y la corrida siguiente murió con
`line 7: unexpected EOF while looking for matching '"'`, después de haber hecho
todo el trabajo de descargar las ocho listas del SAT.

`bash -n` lee el script y revisa la sintaxis sin ejecutar nada, que es
exactamente lo que hacía falta y cuesta milisegundos.

**Las expresiones `${{ }}` de GitHub se sustituyen antes de llegar a bash**, así
que aquí se reemplazan por un literal inocuo. Si se dejaran tal cual, bash vería
`${{` y se quejaría de algo que en la corrida real no existe.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_WORKFLOWS = sorted((Path(__file__).parent.parent / ".github" / "workflows").glob("*.yml"))

# `${{ ... }}` lo resuelve GitHub antes de invocar la shell. Se cambia por una
# palabra para que el script quede sintácticamente completo; el valor da igual
# porque esto no ejecuta nada.
_EXPR = re.compile(r"\$\{\{[^}]*\}\}")


def _pasos_con_shell(ruta: Path) -> list[tuple[str, str]]:
    """(nombre del paso, script) de cada `run:` del workflow."""
    doc = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    out = []
    for nombre_job, job in (doc.get("jobs") or {}).items():
        for i, paso in enumerate(job.get("steps") or []):
            script = paso.get("run")
            if not script:
                continue
            etiqueta = paso.get("name") or f"{nombre_job}[{i}]"
            out.append((f"{ruta.name}::{etiqueta}", script))
    return out


_PASOS = [p for r in _WORKFLOWS for p in _pasos_con_shell(r)]


def test_hay_pasos_de_shell_que_revisar():
    """Si esto queda vacío, los parametrizados de abajo no corren y el archivo
    pasaría en verde sin haber revisado un solo script."""
    assert len(_PASOS) >= 3, _PASOS


@pytest.mark.skipif(shutil.which("bash") is None, reason="no hay bash")
@pytest.mark.parametrize(("etiqueta", "script"), _PASOS, ids=[p[0] for p in _PASOS])
def test_el_script_del_paso_parsea(etiqueta: str, script: str):
    limpio = _EXPR.sub("EXPRESION_DE_GITHUB", script)
    r = subprocess.run(
        ["bash", "-n"], input=limpio, capture_output=True, text=True, check=False
    )
    assert r.returncode == 0, (
        f"el shell de {etiqueta} no parsea:\n{r.stderr}\n"
        "Ojo con las comillas sin cerrar y con los heredoc: el YAML acepta "
        "los dos sin quejarse, y la corrida muere después de hacer el trabajo."
    )
