"""Pruebas de la lógica de riesgo y normalización de RFC."""
from sat69 import risk


def test_normaliza_rfc():
    assert risk.normalizar_rfc(" aaa080808hl8 ") == "AAA080808HL8"
    assert risk.normalizar_rfc("aaa-080808-hl8") == "AAA080808HL8"


def test_rfc_valido():
    assert risk.rfc_valido("AAA080808HL8")      # moral (12)
    assert risk.rfc_valido("AAGL5405077Y7")     # física (13)
    assert not risk.rfc_valido("NOPE")


def test_69b_definitivo_es_critico():
    riesgo, _ = risk.evaluar([], ["Definitivo"])
    assert riesgo == "CRITICO"


def test_69b_presunto_es_alto():
    riesgo, _ = risk.evaluar([], ["Presunto"])
    assert riesgo == "ALTO"


def test_69b_sentencia_favorable_es_bajo():
    riesgo, _ = risk.evaluar([], ["Sentencia Favorable"])
    assert riesgo == "BAJO"


def test_69_firmes_es_medio():
    riesgo, _ = risk.evaluar(["FIRMES"], [])
    assert riesgo == "MEDIO"


def test_69_condonados_es_informativo():
    riesgo, _ = risk.evaluar(["CONDONADOS"], [])
    assert riesgo == "INFORMATIVO"


def test_sin_coincidencias_es_limpio():
    riesgo, _ = risk.evaluar([], [])
    assert riesgo == "LIMPIO"


def test_69b_manda_sobre_69():
    # Si está en ambos, gana la severidad del 69-B definitivo.
    riesgo, _ = risk.evaluar(["FIRMES"], ["Definitivo"])
    assert riesgo == "CRITICO"
