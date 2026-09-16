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
# de los dos habla por debajo. Se eligió Groq como opción por defecto porque su capa
# gratuita no requiere tarjeta de crédito y es suficiente para el volumen de este dataset
# (~665 conversaciones vinculadas); Anthropic queda disponible como alternativa si en algún
# momento se dispone de presupuesto y se prioriza calidad de extracción sobre costo.
PROVEEDOR_LLM = os.getenv("PROVEEDOR_LLM", "groq")  # "groq" | "anthropic"

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

_MODELO_POR_DEFECTO = {
    "groq": "llama-3.3-70b-versatile",
    "anthropic": "claude-sonnet-5",
}
# El catálogo de modelos de Groq cambia con más frecuencia que el de Anthropic: si el
# nombre de abajo ya no existe, revisar los modelos vigentes en console.groq.com y
# fijarlo explícitamente en MODELO_LLM dentro de .env.
MODELO_LLM = os.getenv("MODELO_LLM", _MODELO_POR_DEFECTO.get(PROVEEDOR_LLM, ""))

# Concurrencia conservadora por defecto: las cuentas gratuitas de Groq limitan peticiones
# por minuto: valores altos aquí disparan más 429 (rate limit) de los que vale la pena
# absorber con reintentos. Subir si tu plan lo permite.
EXTRACCION_MAX_WORKERS = int(os.getenv("EXTRACCION_MAX_WORKERS", "4"))


def configurar_logging(nivel: int = logging.INFO) -> None:
    """Configura el logging del pipeline con formato uniforme."""
    logging.basicConfig(
        level=nivel,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
