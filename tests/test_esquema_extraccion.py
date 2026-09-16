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
