"""Configuración central del proyecto: rutas, conexión a base de datos y constantes."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

RAIZ = Path(__file__).resolve().parent.parent
DIR_DATOS = RAIZ / "data"
DIR_CRUDO = DIR_DATOS / "raw"
DIR_DB = RAIZ / "db"
DIR_REPORTES = RAIZ / "reportes"

ARCHIVO_LEADS = DIR_CRUDO / "leads.csv"
ARCHIVO_CONVERSACIONES = DIR_CRUDO / "conversaciones.json"
ARCHIVO_CATALOGO = DIR_CRUDO / "catalogo_motos.csv"
ARCHIVO_ASESORES = DIR_CRUDO / "asesores.csv"
ARCHIVO_HISTORICO = DIR_CRUDO / "historico_cierres.csv"

# SQLite por defecto para desarrollo local; Postgres/Supabase en producción vía .env.
# El código del pipeline es idéntico en ambos motores (SQLAlchemy Core).
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DIR_DATOS / 'warehouse.db'}")

# Nombres comerciales de las tres comercializadoras del grupo. El dataset solo trae los
# IDs; los nombres se fijan aquí para que el tablero muestre algo legible.
NOMBRES_EMPRESAS = {
    "EMP-01": "Motos y Motores del Norte S.A.S.",
    "EMP-02": "Comercializadora 2 del grupo",
    "EMP-03": "Comercializadora 3 del grupo",
}

# Umbrales de matching difuso. Ver docs/reglas-normalizacion.md (R3, R6).
UMBRAL_FUZZY_CIUDAD = 90
UMBRAL_FUZZY_MODELO = 88

# Fase 2 - extracción con IA desde conversaciones (ver docs/reglas-normalizacion.md, R13).
#
# El proveedor es intercambiable por diseño: ClienteLLM es un Protocol (src/llm_cliente.py),
# así que la lógica de negocio (prompt, validación, reintento) no sabe ni le importa cuál
# de los tres habla por debajo.
#
# Historial de esta decisión: se empezó con Groq (capa gratuita en la nube) por no
# requerir hardware propio. En la práctica, su cuota diaria (200.000 tokens/modelo)
# resultó más restrictiva de lo esperado — se agotaron gpt-oss-20b Y gpt-oss-120b en un
# mismo día de pruebas, y un modelo alternativo (qwen3.8-27b) resultó incompatible con
# nuestro esquema de tool-calling. Se migró a Ollama local: sin límites de tasa ni cuota
# diaria (el único techo es el hardware propio), verificado con el mismo formato de
# tool-calling que ya funcionaba en Groq. Groq/Anthropic quedan como alternativas
# documentadas y ya probadas, no se eliminó el código — es una variable de entorno, no
# una reescritura, si algún día conviene volver a una API en la nube.
PROVEEDOR_LLM = os.getenv("PROVEEDOR_LLM", "ollama")  # "ollama" | "groq" | "anthropic"

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

_MODELO_POR_DEFECTO = {
    # llama3.1:8b: el estándar de facto para tool-calling en modelos locales abiertos,
    # confirmado con una prueba real (tool_choice forzado -> tool_calls con JSON válido,
    # sin ningún ajuste de prompt adicional a los ya hechos para Groq).
    "ollama": "llama3.1:8b",
    # gpt-oss-120b, no -20b: en pruebas reales contra el dataset completo, -120b dio
    # igual o mejor calidad (confianza promedio 0.925 vs 0.876, 0 fallos de esquema) sin
    # costo extra de velocidad (ambos comparten el mismo tope de 8.000 TPM en la capa
    # gratuita). Ambos modelos se agotaron el mismo día de pruebas (ver arriba).
    "groq": "openai/gpt-oss-120b",
    "anthropic": "claude-sonnet-5",
}
# El catálogo de modelos de Groq cambia con más frecuencia que el de Anthropic: si el
# nombre de abajo ya no existe, revisar los modelos vigentes en console.groq.com y
# fijarlo explícitamente en MODELO_LLM dentro de .env.
MODELO_LLM = os.getenv("MODELO_LLM", _MODELO_POR_DEFECTO.get(PROVEEDOR_LLM, ""))

# Concurrencia. Con Ollama (proveedor por defecto) el techo real es el hardware local, no
# un límite de tasa remoto: un único servidor Ollama sirve un modelo cargado en memoria y
# procesa la mayoría de requests de forma efectivamente secuencial, así que más workers
# no acelera el batch — solo hace que varias conversaciones esperen turno de cómputo al
# mismo tiempo en vez de una detrás de otra en la cola de este mismo proceso. 1 es lo
# correcto para Ollama en hardware sin GPU dedicada para servir varias inferencias en
# paralelo; subir a 2-4 solo vale la pena si se confirma que el servidor local sí
# paraleliza bien (GPU con VRAM de sobra). Con Groq, 2 seguía siendo el balance medido en
# piloto real (8.000 TPM de tope, ~1.200-1.900 tokens por extracción -> ~5 peticiones/min
# sin importar cuántos workers corran).
EXTRACCION_MAX_WORKERS = int(os.getenv("EXTRACCION_MAX_WORKERS", "1" if PROVEEDOR_LLM == "ollama" else "2"))


def configurar_logging(nivel: int = logging.INFO) -> None:
    """Configura el logging del pipeline con formato uniforme."""
    logging.basicConfig(
        level=nivel,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
