"""Cliente LLM para la extracción de la Fase 2.

Se define como un Protocol en vez de importar `anthropic` directamente en
`extraccion.py` para que la lógica de negocio (prompt, validación, reintento) se pueda
testear con un cliente falso, sin red ni API key. `ClienteAnthropic` es la única pieza
que de verdad habla con la API.
"""

from __future__ import annotations

import json
import logging
from typing import Protocol

from src.esquema_extraccion import json_schema_para_tool

log = logging.getLogger(__name__)

NOMBRE_TOOL = "registrar_extraccion"

_SYSTEM_PROMPT = """Eres un analista que lee transcripciones de WhatsApp entre un \
cliente y un asesor de ventas de motos en Colombia, y extrae la información que el \
CLIENTE reveló sobre su intención de compra.

Reglas estrictas:
- Usa únicamente lo que el cliente dijo explícita o implícitamente. No inventes ni \
completes con suposiciones genéricas.
- El precio de lista que cita el ASESOR no es el presupuesto del cliente. Solo cuenta \
una cifra si el CLIENTE la menciona como lo que tiene, puede pagar o dar de inicial.
- Si el cliente cambia de modelo durante la conversación (por ejemplo, el asesor ofrece \
una alternativa más económica y el cliente la acepta), reporta el último modelo que \
el cliente aceptó, no el primero que preguntó.
- Si la conversación no da información para un campo, dilo con null en vez de adivinar.
- Registra el resultado exclusivamente con la herramienta {tool}, sin texto adicional.""".format(
    tool=NOMBRE_TOOL
)

_INSTRUCCION_REINTENTO = """\n\nIMPORTANTE: tu respuesta anterior no cumplió el formato \
exigido. Responde otra vez usando EXCLUSIVAMENTE la herramienta {tool} con todos sus \
campos obligatorios bien tipados (enums en mayúsculas exactas, booleanos true/false, \
números sin comas ni símbolos de moneda).""".format(tool=NOMBRE_TOOL)


def construir_prompt(transcripcion: str) -> str:
    return f"Transcripción de la conversación:\n\n{transcripcion}"


class ClienteLLM(Protocol):
    """Contrato mínimo que necesita `extraccion.py`: texto de conversación -> dict crudo."""

    def extraer(self, transcripcion: str, *, reintento: bool = False) -> dict:
        """Devuelve el `input` crudo del tool call, tal cual lo emitió el modelo.

        No valida ni interpreta nada — eso es responsabilidad de
        `esquema_extraccion.parsear_y_validar`. Puede lanzar cualquier excepción si la
        llamada de red falla o el modelo no invoca la herramienta.
        """
        ...


class ClienteAnthropic:
    """Implementación real sobre la API de Anthropic, usando tool-use forzado.

    Forzar la herramienta (`tool_choice`) en vez de pedir JSON en texto libre es lo que
    hace la salida estructurada confiable: el modelo no puede responder con prosa ni
    envolver el JSON en markdown.
    """

    def __init__(self, api_key: str, modelo: str) -> None:
        import anthropic  # import perezoso: los tests no necesitan el paquete instalado

        self._cliente = anthropic.Anthropic(api_key=api_key)
        self._modelo = modelo
        self._tool = {
            "name": NOMBRE_TOOL,
            "description": "Registra los datos extraídos de la conversación con el cliente.",
            "input_schema": json_schema_para_tool(),
        }

    def extraer(self, transcripcion: str, *, reintento: bool = False) -> dict:
        prompt = construir_prompt(transcripcion)
        if reintento:
            prompt += _INSTRUCCION_REINTENTO

        respuesta = self._cliente.messages.create(
            model=self._modelo,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            tools=[self._tool],
            tool_choice={"type": "tool", "name": NOMBRE_TOOL},
            messages=[{"role": "user", "content": prompt}],
        )

        bloque_tool = next(
            (b for b in respuesta.content if b.type == "tool_use"), None
        )
        if bloque_tool is None:
            raise ValueError("El modelo no invocó la herramienta de extracción")
        return bloque_tool.input


def formatear_transcripcion(mensajes: list[dict]) -> str:
    """Convierte los mensajes de una conversación en texto plano para el prompt.

    `mensajes` viene de la tabla `mensajes` (orden, emisor, hora, texto).
    """
    lineas = [f"[{m['hora'] or '??:??'}] {m['emisor'].upper()}: {m['texto']}" for m in mensajes]
    return "\n".join(lineas)


def dict_a_json_legible(payload: dict) -> str:
    """Solo para logging: representa la salida cruda del LLM de forma compacta."""
    return json.dumps(payload, ensure_ascii=False)
