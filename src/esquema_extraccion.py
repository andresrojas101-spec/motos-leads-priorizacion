"""Esquema de la extracción estructurada desde conversaciones (Fase 2, R13).

Este módulo es la frontera de validación entre "lo que devuelve el LLM" y "lo que entra
a la base de datos". El LLM nunca escribe directo a `enriquecimiento_conversacion`: su
salida cruda pasa primero por `RespuestaExtraccion`, que valida tipos y enums con
Pydantic, y por las reglas semánticas de `validar_semantica()` antes de persistirse.

Los campos siguen literalmente lo que pide el enunciado del assessment: "modelo de
interés, presupuesto o cuota inicial mencionada, forma de pago, intención declarada,
objeción principal y si pidió cita o cotización".
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, ValidationError


class FormaPago(str, Enum):
    CONTADO = "CONTADO"
    CREDITO = "CREDITO"
    NO_INFORMA = "NO_INFORMA"


class Intencion(str, Enum):
    """Qué tan lista está la persona para avanzar, según lo que dijo en el chat.

    ALTA: pide visitar/comprar ahora, acepta el estudio de crédito, confirma cita.
    MEDIA: pide cotización formal, da cifras concretas, pero no cierra en la conversación.
    BAJA: dice estar "solo mirando", "comparando", o no vuelve a responder.
    """

    ALTA = "ALTA"
    MEDIA = "MEDIA"
    BAJA = "BAJA"


class ObjecionPrincipal(str, Enum):
    """Categorías observadas en los 677 chats de muestra durante la exploración."""

    NINGUNA = "NINGUNA"
    PRECIO = "PRECIO"
    SIN_CUOTA_INICIAL = "SIN_CUOTA_INICIAL"
    SOLO_COMPARANDO = "SOLO_COMPARANDO"
    CONSULTA_CON_TERCERO = "CONSULTA_CON_TERCERO"
    BUSCA_USADO = "BUSCA_USADO"
    OTRA = "OTRA"


# Rango plausible de cuota inicial/presupuesto para una moto en el catálogo (COP).
# catalogo_motos.csv va de $4.990.000 a $24.900.000; una cifra fuera de este rango es
# casi con certeza un error de lectura del LLM (p.ej. confundir el precio de lista que
# menciona el asesor con lo que aportó el cliente), no un dato real del cliente.
PRESUPUESTO_MIN, PRESUPUESTO_MAX = 50_000, 30_000_000


class RespuestaExtraccion(BaseModel):
    """Forma exacta que debe tener la salida del LLM (se usa como `input_schema` del tool).

    Todo campo es opcional salvo `confianza_global`: una conversación de "solo estaba
    mirando" legítimamente no revela modelo, presupuesto ni forma de pago, y eso no es
    una falla de extracción — es información real sobre el lead (baja intención).
    """

    modelo_interes_texto: str | None = Field(
        default=None,
        description=(
            "Modelo de moto que el CLIENTE terminó mostrando interés en comprar, tal "
            "como lo menciona en el chat (puede cambiar durante la conversación si el "
            "asesor ofrece una alternativa y el cliente la acepta). None si no lo dice."
        ),
    )
    presupuesto_monto: int | None = Field(
        default=None,
        description=(
            "Monto en pesos colombianos que el CLIENTE dice tener disponible como cuota "
            "inicial o presupuesto total, convertido a un entero (p.ej. '1 millonzito' -> "
            "1000000, '500mil' -> 500000). NO es el precio de lista que cita el asesor."
        ),
    )
    forma_pago: FormaPago = Field(default=FormaPago.NO_INFORMA)
    intencion: Intencion
    objecion_principal: ObjecionPrincipal
    pidio_cita: bool = Field(
        description="True si el cliente pide o acepta visitar el punto de venta."
    )
    pidio_cotizacion: bool = Field(
        description="True si el cliente pide, o acepta que le envíen, una cotización formal."
    )
    confianza_global: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Qué tan seguro estás de los campos que SÍ llenaste (no de cuántos llenaste). "
            "1.0 = el cliente lo dijo explícitamente. Baja si tuviste que inferir."
        ),
    )


def json_schema_para_tool() -> dict:
    """Convierte el modelo Pydantic al `input_schema` que exige la API de Anthropic."""
    esquema = RespuestaExtraccion.model_json_schema()
    esquema.pop("title", None)
    esquema.pop("description", None)
    return esquema


class ResultadoValidacion(BaseModel):
    """Salida de validar_semantica(): la respuesta ya limpiada, lista para persistir."""

    datos: RespuestaExtraccion
    presupuesto_descartado: bool = False


def parsear_y_validar(payload: dict) -> RespuestaExtraccion:
    """Valida tipos/enums. Lanza pydantic.ValidationError si la forma no coincide.

    Es la primera puerta del gate de calidad: una respuesta que no calza con el esquema
    (JSON mal formado, enum inventado, campo faltante) dispara el reintento en R13.
    """
    return RespuestaExtraccion.model_validate(payload)


def validar_semantica(datos: RespuestaExtraccion) -> ResultadoValidacion:
    """Segunda puerta: reglas de negocio que Pydantic no puede expresar por sí solo.

    Un presupuesto fuera del rango plausible no invalida toda la extracción —se
    descarta solo ese campo y se registra el evento, siguiendo el mismo principio de
    Fase 1: preferir un campo nulo a un dato inventado o mal leído.
    """
    presupuesto_descartado = False
    if datos.presupuesto_monto is not None and not (
        PRESUPUESTO_MIN <= datos.presupuesto_monto <= PRESUPUESTO_MAX
    ):
        datos = datos.model_copy(update={"presupuesto_monto": None})
        presupuesto_descartado = True

    return ResultadoValidacion(datos=datos, presupuesto_descartado=presupuesto_descartado)


__all__ = [
    "FormaPago",
    "Intencion",
    "ObjecionPrincipal",
    "RespuestaExtraccion",
    "ResultadoValidacion",
    "ValidationError",
    "json_schema_para_tool",
    "parsear_y_validar",
    "validar_semantica",
]
