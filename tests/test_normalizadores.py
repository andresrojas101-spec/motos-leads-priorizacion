"""Tests de los normalizadores (R1-R6, R11, R12).

Los casos provienen de valores reales observados en leads.csv durante la exploración,
no de ejemplos inventados: cada uno documenta una inconsistencia concreta del dataset.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.normalizadores import (
    EmparejadorModelos,
    completitud_nombre,
    normalizar_canal,
    normalizar_ciudad,
    normalizar_email,
    normalizar_estado_gestion,
    normalizar_nombre,
    normalizar_telefono,
    parsear_fecha,
)

# --------------------------------------------------------------------------------------
# R1 - Teléfono
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "crudo,esperado",
    [
        ("3128183051", "3128183051"),
        ("+57 350 7959623", "3507959623"),
        ("(322) 315-6416", "3223156416"),
        ("320-637-4600", "3206374600"),
        ("300 501 5843", "3005015843"),
        ("57 311 7200146", "3117200146"),
    ],
)
def test_telefono_formatos_validos(crudo, esperado):
    resultado = normalizar_telefono(crudo)
    assert resultado.valido
    assert resultado.numero == esperado


@pytest.mark.parametrize("crudo", ["300123", "", None, "abc", "1234567890"])
def test_telefono_invalido(crudo):
    """'300123' es la fila de prueba LD-01501; '1234567890' no empieza por 3."""
    resultado = normalizar_telefono(crudo)
    assert not resultado.valido
    assert resultado.numero is None


# --------------------------------------------------------------------------------------
# R2 - Fechas
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "crudo,esperado",
    [
        ("2026-08-21 21:33:00", datetime(2026, 8, 21, 21, 33)),
        ("2026-08-21T16:24:00", datetime(2026, 8, 21, 16, 24)),
        ("21-08-2026", datetime(2026, 8, 21)),
    ],
)
def test_fecha_formatos_directos(crudo, esperado):
    resultado = parsear_fecha(crudo)
    assert resultado.fecha == esperado
    assert resultado.confianza == "exacta"


def test_fecha_barra_dia_mayor_a_12_es_dd_mm():
    """'23/08/2026' solo puede ser DD/MM: no existe el mes 23."""
    resultado = parsear_fecha("23/08/2026 06:39")
    assert resultado.fecha == datetime(2026, 8, 23, 6, 39)
    assert resultado.confianza == "exacta"


def test_fecha_barra_segundo_mayor_a_12_es_mm_dd():
    """'08/18/2026' solo puede ser MM/DD: no existe el mes 18."""
    resultado = parsear_fecha("08/18/2026 01:13")
    assert resultado.fecha == datetime(2026, 8, 18, 1, 13)
    assert resultado.confianza == "exacta"


def test_fecha_barra_ambigua_asume_dd_mm_y_marca_confianza():
    """Ambos componentes <= 12: irresoluble. Se asume DD/MM y queda marcado."""
    resultado = parsear_fecha("09/04/2026 17:25")
    assert resultado.fecha == datetime(2026, 4, 9, 17, 25)
    assert resultado.confianza == "ambigua"


def test_fecha_invalida_dia_33():
    """Fila de prueba LD-01501: '2026-08-33 10:00:00'."""
    resultado = parsear_fecha("2026-08-33 10:00:00")
    assert resultado.fecha is None
    assert resultado.motivo == "fecha_invalida"


def test_fecha_vacia():
    assert parsear_fecha(None).fecha is None
    assert parsear_fecha("").motivo == "fecha_vacia"


# --------------------------------------------------------------------------------------
# R3 - Ciudad
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "crudo,esperado",
    [
        ("Bogotá D.C.", "Bogotá"),
        ("Bogota DC", "Bogotá"),
        ("BOGOTA", "Bogotá"),
        ("bogotá", "Bogotá"),
        ("Medellín ", "Medellín"),
        ("medellin", "Medellín"),
        ("B/quilla", "Barranquilla"),
        ("barranquilla", "Barranquilla"),
        ("Sta Marta", "Santa Marta"),
        ("Rio Negro", "Rionegro"),
        ("RIONEGRO", "Rionegro"),
        ("Itagui", "Itagüí"),
        ("Cartagena de Indias", "Cartagena"),
    ],
)
def test_ciudad_variantes(crudo, esperado):
    assert normalizar_ciudad(crudo).ciudad == esperado


def test_ciudad_no_resuelta_no_inventa():
    resultado = normalizar_ciudad("XYZ123")
    assert resultado.ciudad is None
    assert resultado.metodo == "sin_match"


# --------------------------------------------------------------------------------------
# R4 / R5 - Categóricos
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "crudo,esperado",
    [
        ("WhatsApp", "WHATSAPP"), ("WHATSAPP", "WHATSAPP"), ("whatsapp", "WHATSAPP"),
        ("Meta Ads", "META_ADS"), ("meta ads", "META_ADS"), ("META ADS", "META_ADS"),
        ("Formulario Web", "FORMULARIO_WEB"), ("formulario web", "FORMULARIO_WEB"),
        (None, "DESCONOCIDO"),
    ],
)
def test_canal(crudo, esperado):
    assert normalizar_canal(crudo) == esperado


@pytest.mark.parametrize(
    "crudo,esperado",
    [
        ("Sin gestión", "SIN_GESTION"), ("sin gestion", "SIN_GESTION"), ("SIN GESTION", "SIN_GESTION"),
        ("Cotización enviada", "COTIZACION_ENVIADA"),
        ("No contesta", "NO_CONTESTA"), ("no contesta", "NO_CONTESTA"),
        ("Descartado", "DESCARTADO"),
    ],
)
def test_estado_gestion(crudo, esperado):
    assert normalizar_estado_gestion(crudo) == esperado


# --------------------------------------------------------------------------------------
# R6 - Modelo
# --------------------------------------------------------------------------------------


@pytest.fixture
def emparejador():
    catalogo = [
        {"sku": "SKU-001", "marca": "Honda", "linea": "CB 125F Twister"},
        {"sku": "SKU-003", "marca": "Honda", "linea": "CB 190R"},
        {"sku": "SKU-004", "marca": "Honda", "linea": "Navi"},
        {"sku": "SKU-008", "marca": "Bajaj", "linea": "Pulsar NS 125"},
        {"sku": "SKU-010", "marca": "Bajaj", "linea": "Pulsar RS 200"},
        {"sku": "SKU-012", "marca": "Bajaj", "linea": "Discover 125"},
        {"sku": "SKU-013", "marca": "Suzuki", "linea": "GN 125"},
        {"sku": "SKU-020", "marca": "AKT", "linea": "Evo RS 150"},
    ]
    return EmparejadorModelos(catalogo)


def test_modelo_exacto(emparejador):
    r = emparejador.emparejar("Bajaj Discover 125")
    assert (r.sku, r.metodo, r.confianza) == ("SKU-012", "exacto", 1.0)


def test_modelo_solo_marca_no_adivina_sku(emparejador):
    r = emparejador.emparejar("Bajaj")
    assert r.sku is None
    assert r.marca == "Bajaj"
    assert r.metodo == "solo_marca"


@pytest.mark.parametrize(
    "texto,sku",
    [("Navi", "SKU-004"), ("GN 125", "SKU-013"), ("Bajaj Discover", "SKU-012"), ("Evo RS 150", "SKU-020")],
)
def test_modelo_contencion_unica_resuelve_truncados(emparejador, texto, sku):
    r = emparejador.emparejar(texto)
    assert r.sku == sku
    assert r.metodo == "contencion_unica"


@pytest.mark.parametrize("texto", ["Honda CB", "Bajaj Pulsar"])
def test_modelo_ambiguo_conserva_marca_sin_adivinar(emparejador, texto):
    """'Honda CB' encaja en dos SKU: resolver a uno sería inventar información."""
    r = emparejador.emparejar(texto)
    assert r.sku is None
    assert r.metodo == "ambiguo"
    assert r.marca is not None


@pytest.mark.parametrize(
    "texto,sku",
    [("Suzuky GN 125", "SKU-013"), ("Bajai Pulsar RS 200", "SKU-010"), ("Honda Navi 2026", "SKU-004")],
)
def test_modelo_fuzzy_tolera_errores_de_digitacion(emparejador, texto, sku):
    r = emparejador.emparejar(texto)
    assert r.sku == sku
    assert r.metodo == "fuzzy"


def test_modelo_vacio(emparejador):
    assert emparejador.emparejar(None).metodo == "vacio"


# --------------------------------------------------------------------------------------
# R11 / R12 - Email y nombre
# --------------------------------------------------------------------------------------


def test_email_valido_e_invalido():
    assert normalizar_email("  Adriana.Gomez37@Outlook.com ") == "adriana.gomez37@outlook.com"
    assert normalizar_email("sin-arroba") is None
    assert normalizar_email(None) is None


def test_nombre_title_case_preserva_acentos():
    assert normalizar_nombre("JULIÁN PÉREZ ARIAS") == "Julián Pérez Arias"
    assert normalizar_nombre("marcela ramirez giraldo") == "Marcela Ramirez Giraldo"


def test_completitud_nombre_prefiere_el_mas_completo():
    """Base de R8: el mismo cliente llega abreviado por un canal y completo por otro."""
    assert completitud_nombre("Julián Pérez Arias") > completitud_nombre("J. Pérez Arias")
