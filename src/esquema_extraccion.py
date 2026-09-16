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


# Campos que deben quedar en uno de los valores de su Enum tras normalizar.
_CAMPOS_ENUM = frozenset({"forma_pago", "intencion", "objecion_principal"})
# Campos booleanos que un modelo local a veces serializa como texto ("true"/"false").
_CAMPOS_BOOL = frozenset({"pidio_cita", "pidio_cotizacion"})
# Variantes textuales de "sin valor" observadas en modelos locales (Ollama): en vez de
# omitir la clave o escribir JSON null, escriben la palabra "null" como STRING.
_NULOS_LITERALES = frozenset({"null", "none", "nil", "n/a", ""})


def _normalizar_tipos_laxos(payload: dict) -> dict:
    """Corrige quirks de *formato* observados en modelos locales antes de la validación
    estricta de Pydantic — nunca quirks de *significado*.

    Encontrado corriendo `llama3.2:3b` y, con menor frecuencia, `llama3.1:8b`: el modelo
    entiende el contenido correctamente pero no sostiene el tipado estricto del JSON a
    lo largo de toda la respuesta. Ejemplos reales capturados en logs:
    - `"presupuesto_monto": "null"` (el texto "null" entre comillas, no el literal JSON)
    - `"forma_pago": " credito "` (minúscula y espacios en vez de `"CREDITO"`)
    - `"pidio_cita": "true"` (string en vez de booleano)
    - `"presupuesto_monto": "2000000"` (número como string)

    En todos estos casos el modelo YA decidió el valor correcto — solo lo escribió en el
    tipo equivocado. Corregir la representación no es inventar información.

    Lo que esta función **no** toca, a propósito: una clave ausente por completo, un
    `objecion_principal` con un valor que no existe en el Enum (ej. el real
    `"DATA_CREDITO"`, que el modelo alucinó a partir del contexto de la conversación), o
    un booleano requerido en `None`. Esos son fallos genuinos de seguimiento de
    instrucciones, no de formato — deben seguir disparando el reintento de R13, no
    ocultarse detrás de una normalización silenciosa.
    """
    limpio: dict = {}
    for clave, valor in payload.items():
        if not isinstance(valor, str):
            limpio[clave] = valor
            continue

        v = valor.strip()
        if v.lower() in _NULOS_LITERALES:
            limpio[clave] = None
        elif clave in _CAMPOS_BOOL and v.lower() in ("true", "false"):
            limpio[clave] = v.lower() == "true"
        elif clave == "presupuesto_monto" and v.lstrip("-").isdigit():
            limpio[clave] = int(v)
        elif clave == "confianza_global":
            try:
                limpio[clave] = float(v)
            except ValueError:
                limpio[clave] = v  # deja que Pydantic reporte el error real
        elif clave in _CAMPOS_ENUM:
            limpio[clave] = v.upper()
        else:
            limpio[clave] = v  # otros strings: solo el .strip() ya aplicado

    return limpio


def parsear_y_validar(payload: dict) -> RespuestaExtraccion:
    """Normaliza quirks de formato conocidos y valida tipos/enums.

    Es la primera puerta del gate de calidad: una respuesta que no calza con el esquema
    (JSON mal formado, enum inventado, campo faltante) dispara el reintento en R13,
    incluso después de la normalización — esta solo repara representación, no inventa
    datos que el modelo no proveyó.
    """
    return RespuestaExtraccion.model_validate(_normalizar_tipos_laxos(payload))


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
