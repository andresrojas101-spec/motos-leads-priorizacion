"""Cliente LLM para la extracción de la Fase 2.

Se define como un Protocol en vez de importar `anthropic`/`groq` directamente en
`extraccion.py` para que la lógica de negocio (prompt, validación, reintento) se pueda
testear con un cliente falso, sin red ni API key. `ClienteGroq` y `ClienteAnthropic` son
las únicas piezas que de verdad hablan con una API, y `construir_cliente_llm()` elige
cuál instanciar según `PROVEEDOR_LLM` — cambiar de proveedor es una variable de entorno,
no un cambio de código.
"""

from __future__ import annotations

import json
import logging
from typing import Protocol

from src.esquema_extraccion import FormaPago, Intencion, ObjecionPrincipal, json_schema_para_tool

log = logging.getLogger(__name__)

NOMBRE_TOOL = "registrar_extraccion"


def _lista(enum_cls) -> str:
    return ", ".join(v.value for v in enum_cls)


# Construido desde los mismos enums de Pydantic que validan la respuesta (nunca a mano):
# si el esquema cambia, el prompt no puede quedar desincronizado. Es la reparación
# directa de un fallo real observado con gpt-oss-20b en Groq: el modelo inventaba
# valores como "INTERESADO" o "COMPRAR" para `intencion` porque el prompt anterior no
# enumeraba las opciones válidas de forma explícita, solo confiaba en que el modelo
# leyera el enum del JSON schema — un modelo abierto de 20B no lo hace de forma
# confiable, a diferencia de un modelo propietario más grande.
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
- `modelo_interes_texto` y `presupuesto_monto` SÍ pueden ir null si la conversación no \
da esa información. NUNCA inventes una cifra o un modelo que el cliente no mencionó.
- `forma_pago`, `intencion` y `objecion_principal` son OBLIGATORIOS: jamás pueden ir \
null ni pueden llevar una palabra que no esté en su lista de abajo. Si la conversación \
no da información clara, usa el valor que representa "no sé"/"no aplica" de esa misma \
lista (NO_INFORMA, BAJA o NINGUNA respectivamente) en vez de dejarlo vacío o inventar \
una palabra nueva.
  - forma_pago: EXACTAMENTE uno de estos valores, tal cual: {forma_pago}
  - intencion: EXACTAMENTE uno de estos valores, tal cual: {intencion}
  - objecion_principal: EXACTAMENTE uno de estos valores, tal cual: {objecion}
- Nunca uses sinónimos ni palabras propias para esos tres campos (por ejemplo, nunca \
escribas "INTERESADO" o "COMPRAR": esas palabras no existen en las listas de arriba).
- Registra el resultado exclusivamente con la herramienta {tool}, sin texto adicional.""".format(
    tool=NOMBRE_TOOL,
    forma_pago=_lista(FormaPago),
    intencion=_lista(Intencion),
    objecion=_lista(ObjecionPrincipal),
)

_INSTRUCCION_REINTENTO = """\n\nIMPORTANTE: tu respuesta anterior no cumplió el formato \
exigido. Responde otra vez usando EXCLUSIVAMENTE la herramienta {tool}. Revisa en \
especial `forma_pago`, `intencion` y `objecion_principal`: deben ser EXACTAMENTE uno de \
los valores permitidos que se listaron en las instrucciones (nunca null, nunca una \
palabra inventada). Números sin comas ni símbolos de moneda.""".format(tool=NOMBRE_TOOL)


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


def _tool_formato_openai() -> dict:
    """El bloque `tools` que esperan tanto Groq como Ollama: ambos exponen un endpoint
    compatible con la API de OpenAI, con el schema anidado bajo `function` (a diferencia
    de Anthropic, que lo pone al nivel superior del tool)."""
    return {
        "type": "function",
        "function": {
            "name": NOMBRE_TOOL,
            "description": "Registra los datos extraídos de la conversación con el cliente.",
            "parameters": json_schema_para_tool(),
        },
    }


def _extraer_formato_openai(cliente, modelo: str, transcripcion: str, reintento: bool) -> dict:
    """Cuerpo de `extraer()` compartido por `ClienteGroq` y `ClienteOllama`: ambos usan
    el SDK de `openai` (o uno con la misma interfaz) por debajo, así que el request y la
    forma de leer la respuesta son idénticos — solo cambia cómo se construye `cliente`."""
    prompt = construir_prompt(transcripcion)
    if reintento:
        prompt += _INSTRUCCION_REINTENTO

    respuesta = cliente.chat.completions.create(
        model=modelo,
        max_tokens=1024,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        tools=[_tool_formato_openai()],
        tool_choice={"type": "function", "function": {"name": NOMBRE_TOOL}},
    )

    llamadas = respuesta.choices[0].message.tool_calls
    if not llamadas:
        raise ValueError("El modelo no invocó la herramienta de extracción")
    return json.loads(llamadas[0].function.arguments)


class ClienteGroq:
    """Implementación real sobre la API de Groq (capa gratuita, sin tarjeta).

    Nota de esta sesión: la capa gratuita de Groq resultó más restrictiva de lo
    esperado en la práctica — se agotaron `gpt-oss-20b` Y `gpt-oss-120b` en el mismo
    día (cuota diaria de 200.000 tokens, por modelo). Se mantiene como alternativa
    documentada (cero costo de mantenerla, ya probada), pero `PROVEEDOR_LLM=ollama`
    es la opción activa por defecto — ver `ClienteOllama`.
    """

    def __init__(self, api_key: str, modelo: str) -> None:
        import groq  # import perezoso: los tests no necesitan el paquete instalado

        # timeout total mas corto que el default del SDK (60s): limita cuanto puede
        # tardar UN intento HTTP antes de que el propio cliente lo de por perdido.
        # max_retries=3 (no 5): el SDK solo honra el header Retry-After tal cual si es
        # <=60s (verificado leyendo groq/_base_client.py); para cualquier valor mayor
        # -- como los ~10 minutos que sugiere un 429 de cuota DIARIA -- cae a backoff
        # exponencial con techo de 8s, asi que 5 vs 3 reintentos no cambia el peor caso
        # de forma significativa, pero reduce cuanto tiempo se pierde en casos borde.
        # La garantia real contra que esto vuelva a colgar el batch por horas es el
        # watchdog en ejecutar_extraccion (ver PLAZO_SIN_PROGRESO_SEGUNDOS en
        # extraccion.py), que no depende de que el SDK ni la red se comporten bien.
        self._cliente = groq.Groq(api_key=api_key, max_retries=3, timeout=30.0)
        self._modelo = modelo

    def extraer(self, transcripcion: str, *, reintento: bool = False) -> dict:
        return _extraer_formato_openai(self._cliente, self._modelo, transcripcion, reintento)


class ClienteOllama:
    """Implementación real sobre un servidor Ollama local — sin llave, sin costo,
    sin límites de tasa ni cuota diaria (el único techo es el hardware propio).

    Ollama expone un endpoint compatible con la API de OpenAI en `/v1`, con el mismo
    formato de tool-calling que Groq — confirmado con una prueba real contra
    `llama3.1:8b`: `tool_choice` forzado devuelve `tool_calls` con argumentos ya en
    JSON válido, sin necesitar ningún ajuste de prompt adicional a los ya hechos para
    Groq. Se reusa el SDK de `openai` en vez de hablar HTTP a mano.
    """

    def __init__(self, base_url: str, modelo: str) -> None:
        import openai  # import perezoso: los tests no necesitan el paquete instalado

        # api_key es un valor cualquiera: el servidor local no lo valida, pero el SDK
        # exige que el campo no venga vacío. timeout generoso (120s): un modelo de 8B
        # corriendo en CPU/GPU de escritorio puede tardar bastante mas por request que
        # una API en la nube con hardware dedicado.
        self._cliente = openai.OpenAI(base_url=base_url, api_key="ollama", timeout=120.0)
        self._modelo = modelo

    def extraer(self, transcripcion: str, *, reintento: bool = False) -> dict:
        return _extraer_formato_openai(self._cliente, self._modelo, transcripcion, reintento)


def construir_cliente_llm() -> "ClienteLLM":
    """Instancia el cliente real según `PROVEEDOR_LLM` (ver src/config.py).

    Punto único donde vive la decisión de proveedor. `pipeline.py` no importa ningún
    cliente concreto directamente para que agregar un proveedor nuevo no requiera tocar
    el punto de entrada del pipeline. Ollama no necesita llave (servidor local) — por
    eso no encaja en el mismo patrón de validación que Groq/Anthropic y se maneja aparte.
    """
    from src.config import (
        ANTHROPIC_API_KEY,
        GROQ_API_KEY,
        MODELO_LLM,
        OLLAMA_BASE_URL,
        PROVEEDOR_LLM,
    )

    if PROVEEDOR_LLM == "ollama":
        return ClienteOllama(base_url=OLLAMA_BASE_URL, modelo=MODELO_LLM)
    if PROVEEDOR_LLM == "groq":
        if not GROQ_API_KEY:
            raise RuntimeError(
                "GROQ_API_KEY no está configurada. Copia .env.example a .env y "
                "completa la llave antes de correr la Fase 2 (proveedor: groq)."
            )
        return ClienteGroq(api_key=GROQ_API_KEY, modelo=MODELO_LLM)
    if PROVEEDOR_LLM == "anthropic":
        if not ANTHROPIC_API_KEY:
            raise RuntimeError(
                "ANTHROPIC_API_KEY no está configurada. Copia .env.example a .env y "
                "completa la llave antes de correr la Fase 2 (proveedor: anthropic)."
            )
        return ClienteAnthropic(api_key=ANTHROPIC_API_KEY, modelo=MODELO_LLM)
    raise RuntimeError(
        f"PROVEEDOR_LLM={PROVEEDOR_LLM!r} no reconocido. Usa 'ollama', 'groq' o 'anthropic'."
    )


def formatear_transcripcion(mensajes: list[dict]) -> str:
    """Convierte los mensajes de una conversación en texto plano para el prompt.

    `mensajes` viene de la tabla `mensajes` (orden, emisor, hora, texto).
    """
    lineas = [f"[{m['hora'] or '??:??'}] {m['emisor'].upper()}: {m['texto']}" for m in mensajes]
    return "\n".join(lineas)


def dict_a_json_legible(payload: dict) -> str:
    """Solo para logging: representa la salida cruda del LLM de forma compacta."""
    return json.dumps(payload, ensure_ascii=False)
