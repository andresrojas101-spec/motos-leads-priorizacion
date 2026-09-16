"""Fase 3 - Motor de scoring y priorización.

Metodología completa y justificación con datos reales en docs/scoring.md. Este módulo
implementa esa metodología como funciones puras: reciben valores ya normalizados (de
`leads`/`enriquecimiento_conversacion`) y devuelven un score, sin tocar la base de datos.

Principio heredado de las fases anteriores: **la fórmula nunca inventa información**. Si
un componente no está disponible (sin conversación, extracción fallida), se excluye del
cálculo y los pesos restantes se renormalizan — no se imputa un valor neutro que finja
tener información que no existe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import NamedTuple

VERSION_SCORING = "v1"

# --------------------------------------------------------------------------------------
# Componente 1 - Urgencia (antigüedad sin avance). Ver docs/scoring.md sección 3.
# --------------------------------------------------------------------------------------

# (límite_superior_horas, tasa_cierre_empírica) — de historico_cierres.csv, 2.021 leads
# gestionados. El último balde (>120h) no tiene datos propios: se asume una extrapolación
# conservadora de la tendencia observada, documentada como supuesto en docs/scoring.md.
_BALDES_URGENCIA = (
    (1.0, 0.1512),
    (4.0, 0.1156),
    (24.0, 0.0778),
    (48.0, 0.0769),
    (120.0, 0.0575),
    (float("inf"), 0.035),  # >120h, extrapolado, no medido
)

_TASA_MIN_URGENCIA = min(t for _, t in _BALDES_URGENCIA)
_TASA_MAX_URGENCIA = max(t for _, t in _BALDES_URGENCIA)


def horas_sin_avance(fecha_registro: datetime, fecha_primer_contacto: datetime | None, ahora: datetime) -> float:
    """Horas desde el último evento de avance real del lead.

    Si ya hubo un primer contacto, el reloj de urgencia corre desde ahí (un lead
    contactado hace 5 días y sin resolución está tan frío como uno nunca tocado). Si
    nunca se contactó, corre desde el registro.
    """
    referencia = fecha_primer_contacto or fecha_registro
    return max(0.0, (ahora - referencia).total_seconds() / 3600)


def sub_score_urgencia(horas: float) -> float:
    """Interpola la tasa de cierre empírica del balde correspondiente a una escala 0-100.

    0 = peor balde observado (48-120h), 100 = mejor balde (0-1h). El balde >120h se
    satura en 0 en vez de ir a negativo: ya es el peor caso representable.
    """
    tasa = next(t for limite, t in _BALDES_URGENCIA if horas <= limite)
    return 100 * (tasa - _TASA_MIN_URGENCIA) / (_TASA_MAX_URGENCIA - _TASA_MIN_URGENCIA)


# --------------------------------------------------------------------------------------
# Componentes validados (Grupo A) - forma de pago, cuota inicial, pidió cita.
# Ver docs/scoring.md sección 4. Mismas categorías que historico_cierres.csv Y que el
# esquema de extracción de la Fase 2 (src/esquema_extraccion.py), a propósito: permite
# reusar el mismo criterio sin importar si el dato viene del histórico o de un lead vivo.
# --------------------------------------------------------------------------------------

# CONTADO recibe bono por lógica de negocio (sin fricción de aprobación de crédito).
# NO_INFORMA no se premia aunque el dato crudo lo favorezca levemente: es contraintuitivo
# y estadísticamente ruidoso (ver docs/scoring.md, "Decisión de criterio, no solo de datos").
_SUB_SCORE_FORMA_PAGO = {"CONTADO": 80.0, "NO_INFORMA": 50.0, "CREDITO": 40.0}

_SUB_SCORE_CUOTA_INICIAL = {"SI": 100.0, "NO": 23.1, "NO_INFORMA": 0.0}

_SUB_SCORE_PIDIO_CITA = {True: 100.0, False: 0.0}


def sub_score_forma_pago(valor: str) -> float:
    return _SUB_SCORE_FORMA_PAGO[valor]


def sub_score_cuota_inicial(manifesto: str) -> float:
    """`manifesto` viene tal cual de historico_cierres.csv: 'SI' | 'NO' | 'NO_INFORMA'."""
    return _SUB_SCORE_CUOTA_INICIAL[manifesto]


def sub_score_pidio_cita(pidio: bool) -> float:
    return _SUB_SCORE_PIDIO_CITA[pidio]


# --------------------------------------------------------------------------------------
# Componentes de IA (Grupo B) - razonados, sin histórico que los valide.
# Ver docs/scoring.md sección 5.
# --------------------------------------------------------------------------------------

_SUB_SCORE_INTENCION = {"ALTA": 100.0, "MEDIA": 50.0, "BAJA": 0.0}

_SUB_SCORE_OBJECION = {
    "NINGUNA": 100.0,
    "CONSULTA_CON_TERCERO": 50.0,
    "OTRA": 50.0,
    "PRECIO": 40.0,
    "SIN_CUOTA_INICIAL": 30.0,
    "SOLO_COMPARANDO": 20.0,
    "BUSCA_USADO": 10.0,
}

_SUB_SCORE_PIDIO_COTIZACION = {True: 100.0, False: 0.0}


def sub_score_intencion(intencion: str) -> float:
    return _SUB_SCORE_INTENCION[intencion]


def sub_score_objecion(objecion: str) -> float:
    return _SUB_SCORE_OBJECION[objecion]


def sub_score_pidio_cotizacion(pidio: bool) -> float:
    return _SUB_SCORE_PIDIO_COTIZACION[pidio]


def sub_score_presupuesto_presente(presupuesto_monto: int | None) -> float:
    """Proxy directo de 'manifestó cuota inicial' cuando el dato viene de la IA."""
    return 100.0 if presupuesto_monto is not None else 0.0


def degradar_por_confianza(sub_score_crudo: float, confianza_global: float) -> float:
    """Acerca un sub-score de IA al punto neutral (50) en proporción a la incertidumbre.

    confianza=1.0 -> sin cambio. confianza=0.0 -> colapsa a 50 (no suma ni resta).
    Ver docs/scoring.md sección 5.
    """
    return 50.0 + confianza_global * (sub_score_crudo - 50.0)


# --------------------------------------------------------------------------------------
# Combinación ponderada con renormalización cuando faltan componentes.
# Ver docs/scoring.md sección 6.
# --------------------------------------------------------------------------------------

PESO_URGENCIA = 0.45
PESO_INTENCION = 0.20
PESO_FORMA_PAGO = 0.10
PESO_CUOTA_INICIAL = 0.10
PESO_PIDIO_CITA = 0.10
PESO_OBJECION = 0.05


class ComponenteScore(NamedTuple):
    nombre: str
    peso: float
    sub_score: float


@dataclass
class ResultadoScore:
    score_total: float
    temperatura: str
    componentes: list[ComponenteScore] = field(default_factory=list)

    def desglose(self) -> dict:
        """Serializable para la columna JSON `lead_scores.desglose` (auditoría)."""
        return {
            "version": VERSION_SCORING,
            "score_total": round(self.score_total, 2),
            "temperatura": self.temperatura,
            "componentes": [
                {"nombre": c.nombre, "peso": c.peso, "sub_score": round(c.sub_score, 2)}
                for c in self.componentes
            ],
        }


UMBRAL_CALIENTE = 65.0
UMBRAL_TIBIO = 40.0


def temperatura_de(score_total: float) -> str:
    if score_total >= UMBRAL_CALIENTE:
        return "CALIENTE"
    if score_total >= UMBRAL_TIBIO:
        return "TIBIO"
    return "FRIO"


@dataclass
class EntradaScore:
    """Todo lo que el motor de scoring necesita para un lead, ya normalizado.

    Los campos de `historico_cierres.csv` (forma_pago, cuota_inicial, pidio_cita) y los
    de la Fase 2 (intencion, objecion_principal, pidio_cotizacion, presupuesto_monto,
    confianza_global) son mutuamente excluyentes en la práctica (una fila de leads.csv no
    tiene ambos), pero el motor los acepta por separado para poder validar la fórmula
    contra el histórico (que no tiene columnas de IA) con el mismo código.
    """

    horas_sin_avance: float

    # Grupo A - disponibles cuando hay enriquecimiento O cuando la fila es del histórico.
    forma_pago: str | None = None
    cuota_inicial: str | None = None  # 'SI' | 'NO' | 'NO_INFORMA'
    pidio_cita: bool | None = None

    # Grupo B - solo con enriquecimiento de IA (Fase 2).
    intencion: str | None = None
    objecion_principal: str | None = None
    pidio_cotizacion: bool | None = None
    presupuesto_monto: int | None = None
    presupuesto_dato_presente: bool = False  # distingue "no aplica" de "es None"
    confianza_global: float | None = None


def calcular_score(entrada: EntradaScore) -> ResultadoScore:
    """Combina los componentes disponibles en un score 0-100 y su temperatura.

    Cada componente ausente se omite del cálculo; los pesos de los componentes presentes
    se renormalizan para sumar 1.0. La urgencia siempre está presente (toda fila tiene
    fecha de registro), así que el score nunca queda sin componentes.
    """
    candidatos: list[ComponenteScore] = [
        ComponenteScore("urgencia", PESO_URGENCIA, sub_score_urgencia(entrada.horas_sin_avance))
    ]

    if entrada.forma_pago is not None:
        candidatos.append(
            ComponenteScore("forma_pago", PESO_FORMA_PAGO, sub_score_forma_pago(entrada.forma_pago))
        )
    if entrada.cuota_inicial is not None:
        candidatos.append(
            ComponenteScore("cuota_inicial", PESO_CUOTA_INICIAL, sub_score_cuota_inicial(entrada.cuota_inicial))
        )
    if entrada.pidio_cita is not None:
        candidatos.append(
            ComponenteScore("pidio_cita", PESO_PIDIO_CITA, sub_score_pidio_cita(entrada.pidio_cita))
        )

    confianza = entrada.confianza_global if entrada.confianza_global is not None else 1.0

    if entrada.intencion is not None:
        crudo = sub_score_intencion(entrada.intencion)
        candidatos.append(ComponenteScore("intencion_ia", PESO_INTENCION, degradar_por_confianza(crudo, confianza)))
    if entrada.objecion_principal is not None:
        crudo = sub_score_objecion(entrada.objecion_principal)
        candidatos.append(ComponenteScore("objecion_ia", PESO_OBJECION, degradar_por_confianza(crudo, confianza)))
    if entrada.pidio_cotizacion is not None and entrada.pidio_cita is None:
        # Solo se usa como sustituto de pidio_cita cuando este no existe (evita duplicar
        # el mismo peso dos veces si algun dia ambos coexisten para el mismo lead).
        crudo = sub_score_pidio_cotizacion(entrada.pidio_cotizacion)
        candidatos.append(ComponenteScore("pidio_cita", PESO_PIDIO_CITA, degradar_por_confianza(crudo, confianza)))
    if entrada.presupuesto_dato_presente and entrada.cuota_inicial is None:
        crudo = sub_score_presupuesto_presente(entrada.presupuesto_monto)
        candidatos.append(
            ComponenteScore("cuota_inicial", PESO_CUOTA_INICIAL, degradar_por_confianza(crudo, confianza))
        )

    peso_total = sum(c.peso for c in candidatos)
    score_total = sum(c.peso * c.sub_score for c in candidatos) / peso_total

    return ResultadoScore(score_total=score_total, temperatura=temperatura_de(score_total), componentes=candidatos)
