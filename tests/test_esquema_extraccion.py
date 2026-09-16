"""Tests de las dos puertas de validación de la Fase 2 (R13): esquema y semántica."""

from __future__ import annotations

import pytest

from src.esquema_extraccion import (
    PRESUPUESTO_MAX,
    RespuestaExtraccion,
    ValidationError,
    parsear_y_validar,
    validar_semantica,
)

RESPUESTA_VALIDA = {
    "modelo_interes_texto": "Bajaj Discover 125",
    "presupuesto_monto": 1_000_000,
    "forma_pago": "CREDITO",
    "intencion": "ALTA",
    "objecion_principal": "NINGUNA",
    "pidio_cita": True,
    "pidio_cotizacion": False,
    "confianza_global": 0.9,
}


def test_respuesta_valida_pasa_la_primera_puerta():
    datos = parsear_y_validar(RESPUESTA_VALIDA)
    assert isinstance(datos, RespuestaExtraccion)
    assert datos.forma_pago.value == "CREDITO"


def test_campos_opcionales_faltantes_no_rompen_la_validacion():
    """Una conversacion de 'solo estaba mirando' no revela modelo ni presupuesto."""
    minima = {
        "intencion": "BAJA",
        "objecion_principal": "SOLO_COMPARANDO",
        "pidio_cita": False,
        "pidio_cotizacion": False,
        "confianza_global": 0.8,
    }
    datos = parsear_y_validar(minima)
    assert datos.modelo_interes_texto is None
    assert datos.presupuesto_monto is None
    assert datos.forma_pago.value == "NO_INFORMA"


def test_enum_invalido_dispara_validation_error():
    invalida = dict(RESPUESTA_VALIDA, forma_pago="EN_CUOTAS_MAGICAS")
    with pytest.raises(ValidationError):
        parsear_y_validar(invalida)


@pytest.mark.parametrize("campo", ["intencion", "objecion_principal", "pidio_cita", "confianza_global"])
def test_campo_requerido_faltante_dispara_validation_error(campo):
    invalida = {k: v for k, v in RESPUESTA_VALIDA.items() if k != campo}
    with pytest.raises(ValidationError):
        parsear_y_validar(invalida)


def test_confianza_fuera_de_rango_0_1_dispara_validation_error():
    invalida = dict(RESPUESTA_VALIDA, confianza_global=1.5)
    with pytest.raises(ValidationError):
        parsear_y_validar(invalida)


def test_presupuesto_como_string_no_convertible_dispara_validation_error():
    invalida = dict(RESPUESTA_VALIDA, presupuesto_monto="un millon de pesos")
    with pytest.raises(ValidationError):
        parsear_y_validar(invalida)


# --------------------------------------------------------------------------------------
# Normalización de quirks de formato de modelos locales (Ollama) — casos REALES
# capturados corriendo llama3.2:3b y llama3.1:8b, no inventados. La normalización
# corrige representación (mismo valor, tipo distinto); nunca corrige significado
# (campo ausente, enum inventado): esos deben seguir fallando.
# --------------------------------------------------------------------------------------


def test_null_como_string_se_normaliza_a_none():
    """Real: {'presupuesto_monto': 'null'} -- la palabra entre comillas, no JSON null."""
    datos = parsear_y_validar(dict(RESPUESTA_VALIDA, presupuesto_monto="null"))
    assert datos.presupuesto_monto is None


def test_variantes_de_nulo_se_normalizan():
    for variante in ["null", "NULL", "None", "nil", "  null  ", "Nulo", ""]:
        datos = parsear_y_validar(dict(RESPUESTA_VALIDA, presupuesto_monto=variante))
        assert datos.presupuesto_monto is None, f"fallo con variante {variante!r}"


def test_booleano_como_string_se_normaliza():
    """Real: {'pidio_cita': 'true'} en vez de un booleano JSON."""
    datos = parsear_y_validar(dict(RESPUESTA_VALIDA, pidio_cita="true", pidio_cotizacion="false"))
    assert datos.pidio_cita is True
    assert datos.pidio_cotizacion is False


def test_booleano_en_espanol_se_normaliza():
    """Real: {'pidio_cita': 'falso'} -- llama3.1:8b respondio en espanol pese al prompt
    en espanol pidiendo JSON con booleanos; capturado en el batch completo (CONV-00506)."""
    datos = parsear_y_validar(dict(RESPUESTA_VALIDA, pidio_cita="verdadero", pidio_cotizacion="falso"))
    assert datos.pidio_cita is True
    assert datos.pidio_cotizacion is False


def test_numero_como_string_se_normaliza():
    """Real: {'presupuesto_monto': '2000000'} -- entero valido pero entre comillas."""
    datos = parsear_y_validar(dict(RESPUESTA_VALIDA, presupuesto_monto="2000000"))
    assert datos.presupuesto_monto == 2_000_000


def test_confianza_como_string_numerico_se_normaliza():
    datos = parsear_y_validar(dict(RESPUESTA_VALIDA, confianza_global="0.75"))
    assert datos.confianza_global == 0.75


@pytest.mark.parametrize(
    "campo,sucio,limpio",
    [
        ("forma_pago", " credito ", "CREDITO"),
        ("intencion", " media ", "MEDIA"),
        ("objecion_principal", " NINGUNA ", "NINGUNA"),
        ("objecion_principal", " Ninguna", "NINGUNA"),
    ],
)
def test_enum_con_espacios_o_minusculas_se_normaliza(campo, sucio, limpio):
    """Reales: ' credito ', ' media ', ' NINGUNA ', ' Ninguna' -- el modelo entendio el
    valor correcto pero no sostuvo mayusculas exactas ni omitio el espaciado."""
    datos = parsear_y_validar(dict(RESPUESTA_VALIDA, **{campo: sucio}))
    assert getattr(datos, campo).value == limpio


def test_enum_inventado_sigue_fallando_pese_a_la_normalizacion():
    """Real: {'objecion_principal': 'DATA_CREDITO'} -- el modelo alucino una categoria
    que no existe en el esquema. La normalizacion la deja en mayuscula ('DATA_CREDITO')
    pero sigue sin ser un valor valido: no se debe adivinar a cual de las 7 categorias
    reales correspondia, eso es responsabilidad del reintento de R13, no de este parser."""
    invalida = dict(RESPUESTA_VALIDA, objecion_principal="DATA_CREDITO")
    with pytest.raises(ValidationError):
        parsear_y_validar(invalida)


def test_booleano_requerido_en_none_sigue_fallando():
    """Real: {'pidio_cita': None} -- a diferencia de presupuesto_monto (que SI puede ser
    None), pidio_cita es un booleano obligatorio: el cliente pidio cita o no la pidio,
    no hay un tercer estado. Un None aqui es un fallo genuino de instrucciones, no un
    quirk de formato corregible."""
    invalida = dict(RESPUESTA_VALIDA, pidio_cita=None)
    with pytest.raises(ValidationError):
        parsear_y_validar(invalida)


def test_clave_ausente_sigue_fallando_pese_a_la_normalizacion():
    """La normalizacion actua sobre valores presentes con tipo incorrecto -- no puede
    (ni debe) inventar una clave que el modelo omitio por completo."""
    incompleta = {k: v for k, v in RESPUESTA_VALIDA.items() if k != "intencion"}
    with pytest.raises(ValidationError):
        parsear_y_validar(incompleta)


def test_combinacion_realista_de_varios_quirks_a_la_vez():
    """Aproxima una respuesta real de llama3.2:3b: varios quirks de formato juntos, sin
    ningun campo genuinamente ausente ni ningun enum inventado -- debe pasar limpio."""
    sucia = {
        "modelo_interes_texto": "  Bajaj Discover 125  ",
        "presupuesto_monto": "2000000",
        "forma_pago": " credito ",
        "intencion": " alta ",
        "objecion_principal": "Ninguna",
        "pidio_cita": "true",
        "pidio_cotizacion": "false",
        "confianza_global": "0.8",
    }
    datos = parsear_y_validar(sucia)
    assert datos.presupuesto_monto == 2_000_000
    assert datos.forma_pago.value == "CREDITO"
    assert datos.intencion.value == "ALTA"
    assert datos.objecion_principal.value == "NINGUNA"
    assert datos.pidio_cita is True
    assert datos.pidio_cotizacion is False
    assert datos.confianza_global == 0.8


# --------------------------------------------------------------------------------------
# Segunda puerta: reglas semánticas que Pydantic no expresa por sí solo
# --------------------------------------------------------------------------------------


def test_presupuesto_dentro_de_rango_se_conserva():
    datos = parsear_y_validar(RESPUESTA_VALIDA)
    resultado = validar_semantica(datos)
    assert resultado.datos.presupuesto_monto == 1_000_000
    assert resultado.presupuesto_descartado is False


def test_presupuesto_fuera_de_rango_se_descarta_sin_invalidar_el_resto():
    """Un valor como 500.000.000 casi seguro confunde el precio de lista con el aporte
    del cliente: se anula el campo, pero el resto de la extraccion se conserva."""
    datos = parsear_y_validar(dict(RESPUESTA_VALIDA, presupuesto_monto=PRESUPUESTO_MAX + 1))
    resultado = validar_semantica(datos)
    assert resultado.datos.presupuesto_monto is None
    assert resultado.presupuesto_descartado is True
    assert resultado.datos.intencion.value == "ALTA"  # el resto de los campos sigue intacto


def test_presupuesto_none_no_se_marca_como_descartado():
    datos = parsear_y_validar(dict(RESPUESTA_VALIDA, presupuesto_monto=None))
    resultado = validar_semantica(datos)
    assert resultado.presupuesto_descartado is False
