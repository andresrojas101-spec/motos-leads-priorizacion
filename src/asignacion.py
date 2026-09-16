"""Fase 4 - Cálculo de score y asignación de leads a asesores.

Dos responsabilidades separadas a propósito:
1. `calcular_scores`: aplica el motor de la Fase 3 (`src/scoring.py`) a cada lead activo,
   combinando `leads` con `enriquecimiento_conversacion` cuando existe. Produce filas para
   `lead_scores`.
2. `distribuir_leads` / `asignar_leads`: reparte los leads ya scoreados entre los asesores
   de su punto de venta, respetando `capacidad_diaria_leads`. Produce filas para
   `asignaciones` — el entregable central del problema de negocio: "una lista priorizada
   de gestión diaria por asesor".

`distribuir_leads` es una función pura (sin DB) para poder testear la lógica de reparto
de cupo sin fixtures de base de datos, siguiendo el mismo patrón que `src/scoring.py`.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from sqlalchemy import Connection, select

from src import schema
from src.calidad import ColectorMetricas
from src.scoring import VERSION_SCORING, EntradaScore, calcular_score, horas_sin_avance

log = logging.getLogger(__name__)

# Un lead descartado ya tiene desenlace decidido: no vuelve a la bandeja de gestión
# diaria. No existe un estado "Cerrado" en leads.csv (ver R5) — solo en el histórico.
ESTADOS_EXCLUIDOS_DE_ASIGNACION = frozenset({"DESCARTADO"})

_ESTADOS_ENRIQUECIMIENTO_VALIDOS = frozenset({"OK", "REINTENTO_OK"})


# --------------------------------------------------------------------------------------
# 1. Scoring
# --------------------------------------------------------------------------------------


def _leads_activos(conn: Connection) -> list[dict]:
    """Leads elegibles para score/asignación: canónicos y no descartados.

    Un lead secundario (`es_lead_canonico=False`, ver R8) representa a la misma persona
    que su canónico — incluirlo también duplicaría al cliente en la lista del asesor.

    Un lead puede tener más de una conversación vinculada (25 casos reales: el cliente
    escribió por WhatsApp más de una vez). El join contra `enriquecimiento_conversacion`
    produce una fila por conversación, así que tras traer los datos nos quedamos con una
    sola fila por `lead_id`: la de la conversación con `fecha_inicio` más reciente — el
    mismo criterio de "último avance real" que ya usa el componente de urgencia — para
    que el score refleje el estado más actual del cliente, no una mezcla arbitraria.
    """
    consulta = (
        select(
            schema.leads.c.lead_id,
            schema.leads.c.empresa_id,
            schema.leads.c.punto_venta_id,
            schema.leads.c.fecha_registro,
            schema.leads.c.fecha_primer_contacto,
            schema.enriquecimiento_conversacion.c.forma_pago,
            schema.enriquecimiento_conversacion.c.pidio_cita,
            schema.enriquecimiento_conversacion.c.pidio_cotizacion,
            schema.enriquecimiento_conversacion.c.intencion,
            schema.enriquecimiento_conversacion.c.objecion_principal,
            schema.enriquecimiento_conversacion.c.presupuesto_monto,
            schema.enriquecimiento_conversacion.c.confianza_global,
            schema.enriquecimiento_conversacion.c.extraccion_status,
            schema.conversaciones.c.fecha_inicio,
        )
        .select_from(
            schema.leads.outerjoin(
                schema.enriquecimiento_conversacion,
                schema.leads.c.lead_id == schema.enriquecimiento_conversacion.c.lead_id,
            ).outerjoin(
                schema.conversaciones,
                schema.enriquecimiento_conversacion.c.conversacion_id
                == schema.conversaciones.c.conversacion_id,
            )
        )
        .where(schema.leads.c.es_lead_canonico.is_(True))
        .where(schema.leads.c.estado_gestion.notin_(ESTADOS_EXCLUIDOS_DE_ASIGNACION))
    )
    filas = [dict(f) for f in conn.execute(consulta).mappings()]

    mas_reciente_por_lead: dict[str, dict] = {}
    for fila in filas:
        actual = mas_reciente_por_lead.get(fila["lead_id"])
        if actual is None or (fila["fecha_inicio"] or datetime.min) > (
            actual["fecha_inicio"] or datetime.min
        ):
            mas_reciente_por_lead[fila["lead_id"]] = fila
    return list(mas_reciente_por_lead.values())


def _entrada_desde_lead(fila: dict, ahora: datetime) -> EntradaScore:
    """Traduce una fila de `_leads_activos` al contrato de `EntradaScore`.

    Si la extracción de IA no existe o no fue exitosa (`FALLO`, o sin fila porque la
    conversación no llegó a procesarse), el lead se scorea solo con urgencia — el
    mismo comportamiento ya validado en la Fase 3 para el 56% de leads sin conversación.
    """
    tiene_enriquecimiento = fila["extraccion_status"] in _ESTADOS_ENRIQUECIMIENTO_VALIDOS
    return EntradaScore(
        horas_sin_avance=horas_sin_avance(fila["fecha_registro"], fila["fecha_primer_contacto"], ahora),
        forma_pago=fila["forma_pago"] if tiene_enriquecimiento else None,
        pidio_cita=fila["pidio_cita"] if tiene_enriquecimiento else None,
        intencion=fila["intencion"] if tiene_enriquecimiento else None,
        objecion_principal=fila["objecion_principal"] if tiene_enriquecimiento else None,
        pidio_cotizacion=fila["pidio_cotizacion"] if tiene_enriquecimiento else None,
        presupuesto_monto=fila["presupuesto_monto"] if tiene_enriquecimiento else None,
        # True aunque presupuesto_monto sea None: si hubo extraccion exitosa, "el cliente
        # no reveló presupuesto" es informacion real, no ausencia de dato.
        presupuesto_dato_presente=tiene_enriquecimiento,
        confianza_global=fila["confianza_global"] if tiene_enriquecimiento else None,
    )


def calcular_scores(
    conn: Connection, metricas: ColectorMetricas, ahora: datetime | None = None
) -> list[dict]:
    """Aplica el scorecard de la Fase 3 a todos los leads activos. No hace I/O de más:
    lee `leads` + `enriquecimiento_conversacion` una vez, no toca `lead_scores`."""
    ahora = ahora or datetime.now()
    leads = _leads_activos(conn)

    filas = []
    con_enriquecimiento = 0
    for fila in leads:
        entrada = _entrada_desde_lead(fila, ahora)
        if entrada.confianza_global is not None or entrada.intencion is not None:
            con_enriquecimiento += 1
        resultado = calcular_score(entrada)
        filas.append(
            {
                "lead_id": fila["lead_id"],
                "empresa_id": fila["empresa_id"],
                "score_total": resultado.score_total,
                "temperatura": resultado.temperatura,
                "desglose": resultado.desglose(),
                "version_scoring": VERSION_SCORING,
                "fecha_calculo": ahora,
            }
        )

    conteo_temp = {"CALIENTE": 0, "TIBIO": 0, "FRIO": 0}
    for f in filas:
        conteo_temp[f["temperatura"]] += 1

    metricas.registrar("scoring", "leads_scoreados", len(filas))
    metricas.registrar("scoring", "con_enriquecimiento_ia", con_enriquecimiento)
    for temp, n in conteo_temp.items():
        metricas.registrar("scoring_temperatura", temp, n)

    log.info(
        "Scoring calculado: %d leads (%d con enriquecimiento IA) -> CALIENTE=%d TIBIO=%d FRIO=%d",
        len(filas), con_enriquecimiento, conteo_temp["CALIENTE"], conteo_temp["TIBIO"], conteo_temp["FRIO"],
    )
    return filas


def persistir_scores(conn: Connection, filas: list[dict]) -> None:
    """Refresco completo: `lead_scores` siempre refleja el cálculo más reciente.

    A diferencia de Fase 1, esto SÍ es seguro de refrescar por completo en cada corrida:
    ninguna otra tabla tiene una FK hacia `lead_scores`, así que no hay riesgo de destruir
    trabajo de una fase posterior (la lección de `tablas_de_datos()` en `schema.py`).
    """
    conn.execute(schema.lead_scores.delete())
    if filas:
        conn.execute(schema.lead_scores.insert(), filas)


# --------------------------------------------------------------------------------------
# 2. Asignación a asesores
# --------------------------------------------------------------------------------------


def distribuir_leads(
    leads_ordenados: list[dict], asesores: list[dict]
) -> tuple[dict[str, list[dict]], list[dict]]:
    """Reparte `leads_ordenados` (ya ordenados desc. por score) entre `asesores`,
    respetando `capacidad_diaria_leads`, vía round-robin determinista.

    El round-robin (en vez de "llenar al primer asesor hasta el tope, luego el
    siguiente") evita que un solo asesor se quede con todos los leads calientes
    mientras otros no reciben nada: como un asesor con más cupo sigue disponible más
    turnos en la rotación, la distribución queda proporcional a la capacidad de cada
    uno sin necesitar una fórmula de reparto explícita.

    Devuelve `(asignados_por_asesor_id, leads_sin_capacidad)`. No hace I/O.
    """
    orden_asesores = sorted(a["asesor_id"] for a in asesores)
    n = len(orden_asesores)
    asignados: dict[str, list[dict]] = {aid: [] for aid in orden_asesores}

    if n == 0:
        return asignados, list(leads_ordenados)

    capacidad_restante = {a["asesor_id"]: a["capacidad_diaria_leads"] for a in asesores}
    sin_asignar: list[dict] = []
    idx = 0

    for lead in leads_ordenados:
        colocado = False
        for _ in range(n):
            aid = orden_asesores[idx % n]
            idx += 1
            if capacidad_restante[aid] > 0:
                asignados[aid].append(lead)
                capacidad_restante[aid] -= 1
                colocado = True
                break
        if not colocado:
            sin_asignar.append(lead)

    return asignados, sin_asignar


def asignar_leads(
    conn: Connection, scores: list[dict], metricas: ColectorMetricas, fecha: date | None = None
) -> list[dict]:
    """Agrupa los leads scoreados por punto de venta y reparte cada grupo entre sus
    asesores activos. La separación por empresa queda garantizada por construcción:
    cada punto de venta pertenece a una sola empresa (R8), así que un asesor nunca
    puede recibir un lead de otra.
    """
    fecha = fecha or datetime.now().date()

    if not scores:
        metricas.registrar("asignacion", "leads_asignados", 0)
        metricas.registrar("asignacion", "leads_sin_capacidad", 0)
        return []

    lead_ids = [s["lead_id"] for s in scores]
    pv_por_lead = {
        f["lead_id"]: f["punto_venta_id"]
        for f in conn.execute(
            select(schema.leads.c.lead_id, schema.leads.c.punto_venta_id).where(
                schema.leads.c.lead_id.in_(lead_ids)
            )
        ).mappings()
    }

    asesores_activos = [
        dict(f)
        for f in conn.execute(
            select(
                schema.asesores.c.asesor_id,
                schema.asesores.c.punto_venta_id,
                schema.asesores.c.empresa_id,
                schema.asesores.c.capacidad_diaria_leads,
            ).where(schema.asesores.c.activo.is_(True))
        ).mappings()
    ]

    leads_por_pv: dict[str, list[dict]] = {}
    for s in scores:
        leads_por_pv.setdefault(pv_por_lead[s["lead_id"]], []).append(s)

    asesores_por_pv: dict[str, list[dict]] = {}
    for a in asesores_activos:
        asesores_por_pv.setdefault(a["punto_venta_id"], []).append(a)

    filas_asignacion: list[dict] = []
    total_sin_capacidad = 0

    for pv, leads_pv in leads_por_pv.items():
        leads_pv_ordenados = sorted(leads_pv, key=lambda x: x["score_total"], reverse=True)
        asesores_pv = asesores_por_pv.get(pv, [])
        asignados, sin_asignar = distribuir_leads(leads_pv_ordenados, asesores_pv)
        total_sin_capacidad += len(sin_asignar)

        for asesor_id, lista in asignados.items():
            empresa_id = next(a["empresa_id"] for a in asesores_pv if a["asesor_id"] == asesor_id)
            for orden, lead in enumerate(lista, start=1):
                filas_asignacion.append(
                    {
                        "lead_id": lead["lead_id"],
                        "asesor_id": asesor_id,
                        "empresa_id": empresa_id,
                        "fecha_asignacion": fecha,
                        "orden_prioridad": orden,
                    }
                )

    metricas.registrar("asignacion", "leads_asignados", len(filas_asignacion))
    metricas.registrar(
        "asignacion", "leads_sin_capacidad", total_sin_capacidad,
        "exceden la capacidad diaria de su punto de venta; quedan para manana",
    )
    log.info(
        "Asignacion: %d leads asignados, %d sin capacidad disponible hoy",
        len(filas_asignacion), total_sin_capacidad,
    )
    return filas_asignacion


def persistir_asignaciones(conn: Connection, filas: list[dict], fecha: date) -> None:
    """Reemplaza solo la asignación del día indicado — no toca días anteriores, que
    quedan como historial de lo que cada asesor tuvo en su bandeja cada jornada."""
    conn.execute(schema.asignaciones.delete().where(schema.asignaciones.c.fecha_asignacion == fecha))
    if filas:
        conn.execute(schema.asignaciones.insert(), filas)
