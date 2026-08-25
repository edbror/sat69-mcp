"""
Normalización de RFC y evaluación de riesgo sobre las listas del SAT
(Artículos 69 y 69-B del CFF).

El 69-B (operaciones simuladas) tiene prioridad sobre el 69 (situación fiscal
firme) por severidad: un EFOS definitivo implica que los CFDI emitidos no
producen efectos fiscales.
"""
from __future__ import annotations

import re

_RFC_RE = re.compile(r"^[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}$")


def normalizar_rfc(rfc: str) -> str:
    """Mayúsculas, sin espacios ni guiones."""
    return re.sub(r"[^A-Za-z0-9&Ññ]", "", (rfc or "").strip()).upper()


def rfc_valido(rfc: str) -> bool:
    """Valida la forma básica de un RFC (física 13, moral 12)."""
    return bool(_RFC_RE.match(rfc))


def evaluar(
    supuestos_69: list[str],
    situaciones_69b: list[str],
    situaciones_69b_bis: list[str] | None = None,
) -> tuple[str, str]:
    """Determina (riesgo, veredicto) priorizando 69-B > 69-B Bis > 69.

    riesgo ∈ {CRITICO, ALTO, MEDIO, BAJO, INFORMATIVO, LIMPIO}
    """
    # 1) 69-B manda (EFOS: los CFDI no producen efectos fiscales).
    for sit in situaciones_69b:
        s = (sit or "").lower()
        if "definitiv" in s:
            return ("CRITICO",
                    "EFOS DEFINITIVO (Art. 69-B). Operaciones simuladas confirmadas por el "
                    "SAT; los CFDI emitidos NO producen efectos fiscales. No deducir ni "
                    "acreditar sin subsanar.")
        if "presunto" in s:
            return ("ALTO",
                    "EFOS PRESUNTO (Art. 69-B). El SAT presume operaciones inexistentes. "
                    "Riesgo alto; dar seguimiento a la resolución definitiva antes de operar.")
        if "desvirtu" in s:
            return ("BAJO",
                    "Art. 69-B: DESVIRTUÓ la presunción. Aclaró ante el SAT, pero conviene "
                    "conservar el soporte de la operación.")
        if "sentencia" in s or "favorable" in s:
            return ("BAJO",
                    "Art. 69-B: SENTENCIA FAVORABLE. Fue excluido por resolución jurisdiccional.")

    # 2) 69-B Bis: transmisión indebida de pérdidas fiscales. Es señal de riesgo
    # fiscal del contribuyente, pero —a diferencia del 69-B EFOS— no invalida por
    # sí sola los CFDI de la contraparte, por eso topa en MEDIO (no CRÍTICO).
    for sit in (situaciones_69b_bis or []):
        s = (sit or "").lower()
        if "definitiv" in s:
            return ("MEDIO",
                    "Art. 69-B Bis: transmitió INDEBIDAMENTE el derecho a disminuir pérdidas "
                    "fiscales (definitivo). Señal de riesgo fiscal del contribuyente; no "
                    "invalida por sí sola tus CFDI, pero evalúa antes de contratar.")
        if "presunto" in s:
            return ("MEDIO",
                    "Art. 69-B Bis: PRESUNTA transmisión indebida de pérdidas fiscales. En "
                    "proceso; da seguimiento a la resolución definitiva.")
        if "sentencia" in s or "favorable" in s or "desvirtu" in s:
            return ("BAJO",
                    "Art. 69-B Bis: excluido (sentencia favorable / desvirtuó). "
                    "Conserva el soporte de la operación.")

    # 3) 69: situación fiscal firme.
    if supuestos_69:
        up = sorted({(x or "").upper() for x in supuestos_69})
        for s in up:
            if "FIRME" in s or "EXIGIBLE" in s or "NO LOCALIZ" in s:
                return ("MEDIO",
                        f"Aparece en el listado del Art. 69 ({', '.join(up)}). "
                        "Contribuyente con situación fiscal irregular firme; evaluar antes "
                        "de contratar.")
        return ("INFORMATIVO",
                f"Aparece en el listado del Art. 69 ({', '.join(up)}). "
                "Registro de carácter informativo (p. ej. créditos cancelados/condonados).")

    # 4) Sin coincidencias.
    return ("LIMPIO",
            "No aparece en las listas del Art. 69, 69-B ni 69-B Bis del CFF a la "
            "fecha de los datos.")
