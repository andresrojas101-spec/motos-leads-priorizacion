"""Normalizadores de datos crudos. Funciones puras, sin I/O ni dependencia de base de datos.

Cada función implementa una regla documentada en docs/reglas-normalizacion.md y la
referencia por su ID (R1..R12) en el docstring.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import NamedTuple

from rapidfuzz import fuzz, process

from src.config import UMBRAL_FUZZY_CIUDAD, UMBRAL_FUZZY_MODELO

# --------------------------------------------------------------------------------------
# Base de texto
# --------------------------------------------------------------------------------------

_NO_ALFANUMERICO = re.compile(r"[^A-Z0-9 ]")
_ESPACIOS = re.compile(r"\s+")


def quitar_acentos(texto: str) -> str:
    """Descompone y elimina marcas diacríticas: 'Medellín' -> 'Medellin'."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )


def normalizar_texto(valor: object) -> str:
    """Normaliza a mayúsculas sin acentos ni puntuación, con espacios colapsados.

    Es la base de comparación de R3 (ciudad), R4 (canal), R5 (estado) y R6 (modelo).
    """
    if valor is None:
        return ""
    texto = str(valor).strip()
    if not texto or texto.lower() == "nan":
        return ""
    texto = quitar_acentos(texto).upper()
    texto = _NO_ALFANUMERICO.sub(" ", texto)
    return _ESPACIOS.sub(" ", texto).strip()


# --------------------------------------------------------------------------------------
# R1 - Teléfono
# --------------------------------------------------------------------------------------

_CELULAR_COLOMBIANO = re.compile(r"^3\d{9}$")


class ResultadoTelefono(NamedTuple):
    numero: str | None
    valido: bool
    motivo: str | None


def normalizar_telefono(valor: object) -> ResultadoTelefono:
    """R1 - Deja el teléfono en 10 dígitos y valida que sea un celular colombiano.

    Acepta '+57 350 7959623', '(322) 315-6416', '320-637-4600', '3128183051'.
    Rechaza cualquier cosa que no cumpla ^3\\d{9}$ — un número no marcable no permite
    ni deduplicar (R8) ni gestionar el lead.
    """
    digitos = re.sub(r"\D", "", str(valor or ""))

    if not digitos:
        return ResultadoTelefono(None, False, "telefono_vacio")

    if len(digitos) == 12 and digitos.startswith("57"):
        digitos = digitos[2:]

    digitos = digitos[-10:]

    if not _CELULAR_COLOMBIANO.match(digitos):
        return ResultadoTelefono(None, False, "telefono_invalido")

    return ResultadoTelefono(digitos, True, None)


# --------------------------------------------------------------------------------------
# R2 - Fechas
# --------------------------------------------------------------------------------------

_FORMATOS_DIRECTOS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d-%m-%Y", "%Y-%m-%d")
_PATRON_BARRA = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2}):(\d{2}))?$")


class ResultadoFecha(NamedTuple):
    fecha: datetime | None
    confianza: str | None  # 'exacta' | 'ambigua'
    motivo: str | None


def parsear_fecha(valor: object) -> ResultadoFecha:
    """R2 - Parsea las 4 convenciones de fecha presentes y marca las ambiguas.

    Las fechas con '/' donde ambos componentes son <= 12 no permiten distinguir DD/MM de
    MM/DD: se asume DD/MM (convención colombiana) y se devuelve confianza 'ambigua' para
    que el scoring pueda degradar su peso y un analista pueda auditarlo.
    """
    texto = str(valor or "").strip()
    if not texto or texto.lower() in {"nan", "nat", "none"}:
        return ResultadoFecha(None, None, "fecha_vacia")

    for formato in _FORMATOS_DIRECTOS:
        try:
            return ResultadoFecha(datetime.strptime(texto, formato), "exacta", None)
        except ValueError:
            continue

    coincidencia = _PATRON_BARRA.match(texto)
    if coincidencia:
        primero, segundo, anio, hora, minuto = coincidencia.groups()
        primero, segundo = int(primero), int(segundo)
        hora, minuto = int(hora or 0), int(minuto or 0)

        if primero > 12 and segundo <= 12:
            dia, mes, confianza = primero, segundo, "exacta"
        elif segundo > 12 and primero <= 12:
            dia, mes, confianza = segundo, primero, "exacta"
        elif primero <= 12 and segundo <= 12:
            dia, mes, confianza = primero, segundo, "ambigua"
        else:
            return ResultadoFecha(None, None, "fecha_invalida")

        try:
            return ResultadoFecha(datetime(int(anio), mes, dia, hora, minuto), confianza, None)
        except ValueError:
            return ResultadoFecha(None, None, "fecha_invalida")

    # Formato reconocible pero con valores imposibles (ej. '2026-08-33 10:00:00').
    if re.match(r"^\d{4}-\d{2}-\d{2}", texto):
        return ResultadoFecha(None, None, "fecha_invalida")

    return ResultadoFecha(None, None, "fecha_no_reconocida")


# --------------------------------------------------------------------------------------
# R3 - Ciudad
# --------------------------------------------------------------------------------------

CIUDADES_CANONICAS = (
    "Bogotá", "Soacha", "Medellín", "Bello", "Itagüí", "Rionegro",
    "Barranquilla", "Soledad", "Cartagena", "Santa Marta", "Montería",
)

# Alias observados en leads.csv, ya normalizados con normalizar_texto().
_ALIAS_CIUDAD = {
    "BOGOTA": "Bogotá", "BOGOTA DC": "Bogotá", "BOGOTA D C": "Bogotá",
    "SOACHA": "Soacha",
    "MEDELLIN": "Medellín",
    "BELLO": "Bello",
    "ITAGUI": "Itagüí",
    "RIONEGRO": "Rionegro", "RIO NEGRO": "Rionegro",
    "BARRANQUILLA": "Barranquilla", "B QUILLA": "Barranquilla", "BQUILLA": "Barranquilla",
    "SOLEDAD": "Soledad",
    "CARTAGENA": "Cartagena", "CARTAGENA DE INDIAS": "Cartagena",
    "SANTA MARTA": "Santa Marta", "STA MARTA": "Santa Marta",
    "MONTERIA": "Montería",
}

_CANONICAS_NORMALIZADAS = {normalizar_texto(c): c for c in CIUDADES_CANONICAS}


class ResultadoCiudad(NamedTuple):
    ciudad: str | None
    metodo: str  # 'alias' | 'fuzzy' | 'sin_match' | 'vacio'


def normalizar_ciudad(valor: object) -> ResultadoCiudad:
    """R3 - Resuelve las variantes de escritura de ciudad a un nombre canónico.

    'Bogotá D.C.', 'BOGOTA', 'Bogota DC' -> 'Bogotá'. Si no se resuelve devuelve None:
    la ciudad no es crítica para gestionar el lead (el punto de venta ya lo ubica).
    """
    normalizado = normalizar_texto(valor)
    if not normalizado:
        return ResultadoCiudad(None, "vacio")

    if normalizado in _ALIAS_CIUDAD:
        return ResultadoCiudad(_ALIAS_CIUDAD[normalizado], "alias")

    mejor = process.extractOne(
        normalizado, list(_CANONICAS_NORMALIZADAS.keys()), scorer=fuzz.WRatio
    )
    if mejor and mejor[1] >= UMBRAL_FUZZY_CIUDAD:
        return ResultadoCiudad(_CANONICAS_NORMALIZADAS[mejor[0]], "fuzzy")

    return ResultadoCiudad(None, "sin_match")


# --------------------------------------------------------------------------------------
# R4 / R5 - Categóricos
# --------------------------------------------------------------------------------------

_MAPA_CANAL = {
    "WHATSAPP": "WHATSAPP",
    "META ADS": "META_ADS",
    "FORMULARIO WEB": "FORMULARIO_WEB",
}

_MAPA_ESTADO = {
    "SIN GESTION": "SIN_GESTION",
    "CONTACTADO": "CONTACTADO",
    "COTIZACION ENVIADA": "COTIZACION_ENVIADA",
    "EN PROCESO": "EN_PROCESO",
    "NO CONTESTA": "NO_CONTESTA",
    "DESCARTADO": "DESCARTADO",
}

# Estados que implican que hubo contacto con el cliente. Se usan en R9.
ESTADOS_CON_CONTACTO = frozenset(
    {"CONTACTADO", "COTIZACION_ENVIADA", "EN_PROCESO", "NO_CONTESTA", "DESCARTADO"}
)


def normalizar_canal(valor: object) -> str:
    """R4 - Unifica las 9 variantes de escritura de canal en 3 valores canónicos."""
    return _MAPA_CANAL.get(normalizar_texto(valor), "DESCONOCIDO")


def normalizar_estado_gestion(valor: object) -> str:
    """R5 - Unifica las 10 variantes de estado de gestión en 6 valores canónicos."""
    return _MAPA_ESTADO.get(normalizar_texto(valor), "DESCONOCIDO")


# --------------------------------------------------------------------------------------
# R11 / R12 - Email y nombre
# --------------------------------------------------------------------------------------

_PATRON_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")


def normalizar_email(valor: object) -> str | None:
    """R11 - Minúsculas y validación básica de patrón. Inválido -> None."""
    texto = str(valor or "").strip().lower()
    if not texto or texto == "nan":
        return None
    return texto if _PATRON_EMAIL.match(texto) else None


def normalizar_nombre(valor: object) -> str | None:
    """R12 - Title Case preservando acentos: 'JULIÁN PÉREZ' -> 'Julián Pérez'."""
    texto = _ESPACIOS.sub(" ", str(valor or "").strip())
    if not texto or texto.lower() == "nan":
        return None
    return " ".join(p.capitalize() for p in texto.split(" "))


def completitud_nombre(nombre: str | None) -> int:
    """Cuenta tokens con más de una letra: mide qué tan completo es un nombre.

    'J. Pérez Arias' -> 2, 'Julián Pérez Arias' -> 3. Usado en R8 para elegir el nombre
    canónico de una persona entre sus leads duplicados.
    """
    if not nombre:
        return 0
    return sum(1 for token in normalizar_texto(nombre).split() if len(token) > 1)


# --------------------------------------------------------------------------------------
# R6 - Modelo de interés -> SKU
# --------------------------------------------------------------------------------------


class ResultadoModelo(NamedTuple):
    sku: str | None
    marca: str | None
    metodo: str  # exacto | solo_marca | contencion_unica | ambiguo | fuzzy | sin_match | vacio
    confianza: float


class EmparejadorModelos:
    """Resuelve texto libre de modelo contra el catálogo mediante la cascada de R6.

    Se construye una vez con el catálogo y se reutiliza para las 1.503 filas.
    """

    def __init__(self, filas_catalogo: list[dict]) -> None:
        self._por_modelo: dict[str, dict] = {}
        self._marcas: dict[str, str] = {}
        self._tokens: dict[str, set[str]] = {}

        for fila in filas_catalogo:
            clave = normalizar_texto(f"{fila['marca']} {fila['linea']}")
            self._por_modelo[clave] = fila
            self._tokens[clave] = set(clave.split())
            self._marcas[normalizar_texto(fila["marca"])] = fila["marca"]

    def emparejar(self, texto: object) -> ResultadoModelo:
        """R6 - Cascada de 5 pasos; gana el primer acierto.

        Prefiere no resolver antes que resolver mal: un SKU equivocado contamina la
        verificación de inventario en el punto de venta.
        """
        normalizado = normalizar_texto(texto)
        if not normalizado:
            return ResultadoModelo(None, None, "vacio", 0.0)

        # Paso 1 - coincidencia exacta con 'marca + linea'.
        if normalizado in self._por_modelo:
            fila = self._por_modelo[normalizado]
            return ResultadoModelo(fila["sku"], fila["marca"], "exacto", 1.0)

        # Paso 2 - el texto es únicamente una marca ('Bajaj', 'Honda').
        if normalizado in self._marcas:
            return ResultadoModelo(None, self._marcas[normalizado], "solo_marca", 0.5)

        # Paso 3 - los tokens del texto están contenidos en un único modelo del catálogo.
        # Resuelve nombres truncados: 'Navi' -> 'Honda Navi', 'GN 125' -> 'Suzuki GN 125'.
        tokens_texto = set(normalizado.split())
        candidatos = [
            clave for clave, tokens in self._tokens.items() if tokens_texto <= tokens
        ]
        if len(candidatos) == 1:
            fila = self._por_modelo[candidatos[0]]
            return ResultadoModelo(fila["sku"], fila["marca"], "contencion_unica", 0.9)
        if len(candidatos) > 1:
            # 'Honda CB' encaja en 'CB 125F Twister' y 'CB 190R': no se adivina el SKU.
            marcas = {self._por_modelo[c]["marca"] for c in candidatos}
            if len(marcas) == 1:
                return ResultadoModelo(None, marcas.pop(), "ambiguo", 0.5)
            return ResultadoModelo(None, None, "ambiguo", 0.3)

        # Paso 4 - fuzzy para errores de digitación ('Suzuky Gixxer 150', 'Hnda XR 150L').
        mejor = process.extractOne(
            normalizado, list(self._por_modelo.keys()), scorer=fuzz.WRatio
        )
        if mejor and mejor[1] >= UMBRAL_FUZZY_MODELO:
            fila = self._por_modelo[mejor[0]]
            return ResultadoModelo(fila["sku"], fila["marca"], "fuzzy", round(mejor[1] / 100, 3))

        # Paso 5 - sin resolver.
        return ResultadoModelo(None, None, "sin_match", 0.0)
