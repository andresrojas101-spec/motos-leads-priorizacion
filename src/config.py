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
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODELO_LLM = os.getenv("MODELO_LLM", "claude-sonnet-5")
EXTRACCION_MAX_WORKERS = int(os.getenv("EXTRACCION_MAX_WORKERS", "8"))


def configurar_logging(nivel: int = logging.INFO) -> None:
    """Configura el logging del pipeline con formato uniforme."""
    logging.basicConfig(
        level=nivel,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )
