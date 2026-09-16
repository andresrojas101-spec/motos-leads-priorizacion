"""Tests de la Fase 4: scoring persistido + asignación a asesores.

`distribuir_leads` es una función pura (sin BD) — se testea directo. `calcular_scores` y
`asignar_leads` se testean contra una base de datos SQLite en memoria, siguiendo el mismo
patrón que `tests/test_extraccion.py`.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from src import schema
from src.asignacion import (
    asignar_leads,
    calcular_scores,
    distribuir_leads,
    persistir_asignaciones,
    persistir_scores,
)
from src.calidad import ColectorMetricas

# --------------------------------------------------------------------------------------
# distribuir_leads — función pura
# --------------------------------------------------------------------------------------


def _lead(id_, score):
    return {"lead_id": id_, "score_total": score}


def _asesor(id_, capacidad):
    return {"asesor_id": id_, "capacidad_diaria_leads": capacidad}


def test_distribuir_respeta_la_capacidad_de_cada_asesor():
    leads = [_lead(f"L{i}", 100 - i) for i in range(10)]
    asesores = [_asesor("AS-01", 3), _asesor("AS-02", 2)]

    asignados, sin_asignar = distribuir_leads(leads, asesores)

    assert len(asignados["AS-01"]) == 3
    assert len(asignados["AS-02"]) == 2
    assert len(sin_asignar) == 5  # 10 leads - 5 de capacidad total


def test_distribuir_no_deja_leads_sin_asignar_si_hay_cupo_de_sobra():
    leads = [_lead(f"L{i}", 100 - i) for i in range(4)]
    asesores = [_asesor("AS-01", 10)]

    asignados, sin_asignar = distribuir_leads(leads, asesores)

    assert len(asignados["AS-01"]) == 4
    assert sin_asignar == []


def test_distribuir_reparte_proporcional_a_la_capacidad_no_todo_al_primero():
    """El asesor con mas capacidad debe terminar con mas leads, pero ambos deben
    recibir leads de las primeras posiciones (los de mayor score) -- el reparto no debe
    volcar TODOS los mejores leads en un solo asesor mientras el otro no recibe nada."""
    leads = [_lead(f"L{i}", 100 - i) for i in range(12)]
    asesores = [_asesor("AS-01", 8), _asesor("AS-02", 4)]

    asignados, sin_asignar = distribuir_leads(leads, asesores)

    assert len(asignados["AS-01"]) == 8
    assert len(asignados["AS-02"]) == 4
    assert sin_asignar == []
    # AS-02 (menor capacidad) tambien debe tener acceso a leads de score alto temprano,
    # no solo a las sobras: su primer lead asignado debe estar entre los primeros 4.
    primer_lead_as02 = asignados["AS-02"][0]["lead_id"]
    assert primer_lead_as02 in {"L0", "L1", "L2", "L3"}


def test_distribuir_preserva_el_orden_de_score_dentro_de_cada_asesor():
    leads = [_lead(f"L{i}", 100 - i) for i in range(6)]
    asesores = [_asesor("AS-01", 6)]

    asignados, _ = distribuir_leads(leads, asesores)

    scores_asignados = [leads[int(l["lead_id"][1:])]["score_total"] for l in asignados["AS-01"]]
    assert scores_asignados == sorted(scores_asignados, reverse=True)


def test_distribuir_es_determinista_por_asesor_id():
    """Mismo input, mismo resultado — importante para que 'orden_prioridad' sea
    reproducible entre corridas del pipeline, no dependa del orden de un dict."""
    leads = [_lead(f"L{i}", 100 - i) for i in range(9)]
    asesores = [_asesor("AS-03", 3), _asesor("AS-01", 3), _asesor("AS-02", 3)]

    r1, _ = distribuir_leads(leads, asesores)
    r2, _ = distribuir_leads(leads, asesores)

    assert r1 == r2


def test_distribuir_sin_asesores_deja_todo_sin_asignar():
    leads = [_lead("L1", 90)]

    asignados, sin_asignar = distribuir_leads(leads, [])

    assert asignados == {}
    assert len(sin_asignar) == 1


def test_distribuir_sin_leads_no_falla():
    asignados, sin_asignar = distribuir_leads([], [_asesor("AS-01", 5)])
    assert asignados == {"AS-01": []}
    assert sin_asignar == []


# --------------------------------------------------------------------------------------
# calcular_scores / asignar_leads — sobre base de datos en memoria
# --------------------------------------------------------------------------------------


@pytest.fixture
def conn():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    schema.metadata.create_all(engine)
    with engine.connect() as c:
        yield c


def _poblar_base(conn, *, con_enriquecimiento=True):
    conn.execute(schema.empresas.insert(), [{"empresa_id": "EMP-01", "nombre": "Empresa 1"}])
    conn.execute(schema.puntos_venta.insert(), [{"punto_venta_id": "PV-001", "empresa_id": "EMP-01"}])
    conn.execute(
        schema.asesores.insert(),
        [
            {"asesor_id": "AS-01", "nombre": "Asesor Uno", "punto_venta_id": "PV-001", "empresa_id": "EMP-01",
             "capacidad_diaria_leads": 2, "activo": True, "fecha_ingreso": date(2024, 1, 1)},
            {"asesor_id": "AS-02", "nombre": "Asesor Dos", "punto_venta_id": "PV-001", "empresa_id": "EMP-01",
             "capacidad_diaria_leads": 2, "activo": True, "fecha_ingreso": date(2024, 1, 1)},
            # Inactivo: no debe recibir asignaciones aunque tenga capacidad.
            {"asesor_id": "AS-03", "nombre": "Asesor Inactivo", "punto_venta_id": "PV-001", "empresa_id": "EMP-01",
             "capacidad_diaria_leads": 10, "activo": False, "fecha_ingreso": date(2024, 1, 1)},
        ],
    )
    conn.execute(
        schema.personas.insert(),
        [{"persona_id": i, "empresa_id": "EMP-01", "telefono_normalizado": f"300000000{i}", "total_leads": 1}
         for i in range(1, 5)],
    )
    conn.execute(
        schema.leads.insert(),
        [
            # Lead 1: sin contacto, recien registrado -> baja urgencia.
            {"lead_id": "LD-1", "persona_id": 1, "empresa_id": "EMP-01", "punto_venta_id": "PV-001",
             "canal_origen": "WHATSAPP", "fecha_registro": datetime(2026, 9, 15, 9, 0),
             "estado_gestion": "SIN_GESTION", "es_lead_canonico": True},
            # Lead 2: sin contacto hace mucho -> alta urgencia.
            {"lead_id": "LD-2", "persona_id": 2, "empresa_id": "EMP-01", "punto_venta_id": "PV-001",
             "canal_origen": "WHATSAPP", "fecha_registro": datetime(2026, 9, 1, 9, 0),
             "estado_gestion": "SIN_GESTION", "es_lead_canonico": True},
            # Lead 3: descartado -> NO debe entrar a scoring/asignacion.
            {"lead_id": "LD-3", "persona_id": 3, "empresa_id": "EMP-01", "punto_venta_id": "PV-001",
             "canal_origen": "WHATSAPP", "fecha_registro": datetime(2026, 9, 1, 9, 0),
             "estado_gestion": "DESCARTADO", "es_lead_canonico": True},
            # Lead 4: lead secundario (misma persona que otro) -> NO debe entrar.
            {"lead_id": "LD-4", "persona_id": 1, "empresa_id": "EMP-01", "punto_venta_id": "PV-001",
             "canal_origen": "META_ADS", "fecha_registro": datetime(2026, 9, 14, 9, 0),
             "estado_gestion": "SIN_GESTION", "es_lead_canonico": False},
        ],
    )
    if con_enriquecimiento:
        conn.execute(
            schema.conversaciones.insert(),
            [{"conversacion_id": "CONV-1", "lead_id": "LD-1", "lead_id_declarado": "LD-1", "empresa_id": "EMP-01",
              "canal": "WHATSAPP", "fecha_inicio": datetime(2026, 9, 15, 9, 0), "num_mensajes": 2,
              "estado_vinculacion": "VINCULADA"}],
        )
        conn.execute(
            schema.enriquecimiento_conversacion.insert(),
            [{"conversacion_id": "CONV-1", "lead_id": "LD-1", "modelo_interes_texto": "Bajaj Discover 125",
              "sku_interes": None, "presupuesto_monto": 1_000_000, "forma_pago": "CONTADO",
              "intencion": "ALTA", "objecion_principal": "NINGUNA", "pidio_cita": True,
              "pidio_cotizacion": False, "confianza_global": 1.0, "extraccion_status": "OK",
              "detalle_error": None, "modelo_llm": "test", "fecha_extraccion": datetime.now()}],
        )
    conn.commit()


def test_calcular_scores_excluye_descartados_y_secundarios(conn):
    _poblar_base(conn)
    metricas = ColectorMetricas(run_id="test", fase="fase4")

    scores = calcular_scores(conn, metricas, ahora=datetime(2026, 9, 15, 12, 0))

    lead_ids = {s["lead_id"] for s in scores}
    assert lead_ids == {"LD-1", "LD-2"}  # ni LD-3 (descartado) ni LD-4 (secundario)


def test_calcular_scores_lead_con_enriquecimiento_pesa_mas_que_sin_el(conn):
    """LD-1 tiene la MISMA urgencia base que si no tuviera enriquecimiento, pero con
    intencion=ALTA, pidio_cita=True, forma_pago=CONTADO deberia salir con score mayor
    que un lead comparable sin conversacion."""
    _poblar_base(conn)
    metricas = ColectorMetricas(run_id="test", fase="fase4")
    ahora = datetime(2026, 9, 15, 9, 30)  # 30 min despues del registro de LD-1

    scores = calcular_scores(conn, metricas, ahora=ahora)
    por_id = {s["lead_id"]: s for s in scores}

    assert por_id["LD-1"]["temperatura"] == "CALIENTE"
    assert por_id["LD-1"]["score_total"] > 90  # urgencia maxima + todos los bonos de IA


def test_calcular_scores_lead_viejo_sin_enriquecimiento_es_frio(conn):
    _poblar_base(conn)
    metricas = ColectorMetricas(run_id="test", fase="fase4")
    ahora = datetime(2026, 9, 15, 12, 0)  # LD-2 lleva 14 dias sin contacto

    scores = calcular_scores(conn, metricas, ahora=ahora)
    por_id = {s["lead_id"]: s for s in scores}

    assert por_id["LD-2"]["temperatura"] == "FRIO"


def test_calcular_scores_lead_con_dos_conversaciones_no_se_duplica(conn):
    """Real: 25 leads del dataset completo tienen mas de una conversacion vinculada (el
    cliente escribio por WhatsApp mas de una vez). El join contra
    enriquecimiento_conversacion produce una fila por conversacion -- sin deduplicar,
    persistir_scores rompia con UNIQUE constraint sobre lead_id. Debe quedar exactamente
    una fila de score por lead, tomando la conversacion con fecha_inicio mas reciente."""
    _poblar_base(conn, con_enriquecimiento=False)
    conn.execute(
        schema.conversaciones.insert(),
        [
            {"conversacion_id": "CONV-1", "lead_id": "LD-1", "lead_id_declarado": "LD-1",
             "empresa_id": "EMP-01", "canal": "WHATSAPP", "fecha_inicio": datetime(2026, 9, 10, 9, 0),
             "num_mensajes": 2, "estado_vinculacion": "VINCULADA"},
            {"conversacion_id": "CONV-2", "lead_id": "LD-1", "lead_id_declarado": "LD-1",
             "empresa_id": "EMP-01", "canal": "WHATSAPP", "fecha_inicio": datetime(2026, 9, 15, 9, 0),
             "num_mensajes": 3, "estado_vinculacion": "VINCULADA"},
        ],
    )
    conn.execute(
        schema.enriquecimiento_conversacion.insert(),
        [
            {"conversacion_id": "CONV-1", "lead_id": "LD-1", "modelo_interes_texto": "AKT 125",
             "sku_interes": None, "presupuesto_monto": None, "forma_pago": "NO_INFORMA",
             "intencion": "BAJA", "objecion_principal": "SOLO_COMPARANDO", "pidio_cita": False,
             "pidio_cotizacion": False, "confianza_global": 0.6, "extraccion_status": "OK",
             "detalle_error": None, "modelo_llm": "test", "fecha_extraccion": datetime.now()},
            {"conversacion_id": "CONV-2", "lead_id": "LD-1", "modelo_interes_texto": "Bajaj Discover 125",
             "sku_interes": None, "presupuesto_monto": 1_000_000, "forma_pago": "CONTADO",
             "intencion": "ALTA", "objecion_principal": "NINGUNA", "pidio_cita": True,
             "pidio_cotizacion": False, "confianza_global": 1.0, "extraccion_status": "OK",
             "detalle_error": None, "modelo_llm": "test", "fecha_extraccion": datetime.now()},
        ],
    )
    conn.commit()
    metricas = ColectorMetricas(run_id="test", fase="fase4")

    scores = calcular_scores(conn, metricas, ahora=datetime(2026, 9, 15, 9, 30))

    filas_ld1 = [s for s in scores if s["lead_id"] == "LD-1"]
    assert len(filas_ld1) == 1
    # Debe reflejar CONV-2 (la mas reciente: intencion ALTA), no CONV-1 (BAJA).
    assert filas_ld1[0]["temperatura"] == "CALIENTE"


def test_persistir_scores_es_refresco_completo(conn):
    conn.execute(
        schema.lead_scores.insert(),
        [{"lead_id": "LD-VIEJO", "empresa_id": "EMP-01", "score_total": 50.0, "temperatura": "TIBIO",
          "desglose": {}, "version_scoring": "v0", "fecha_calculo": datetime.now()}],
    )
    conn.commit()

    persistir_scores(conn, [{"lead_id": "LD-1", "empresa_id": "EMP-01", "score_total": 90.0,
                              "temperatura": "CALIENTE", "desglose": {}, "version_scoring": "v1",
                              "fecha_calculo": datetime.now()}])
    conn.commit()

    filas = conn.execute(select(schema.lead_scores)).mappings().all()
    assert len(filas) == 1
    assert filas[0]["lead_id"] == "LD-1"


def test_asignar_leads_respeta_tenancy_y_capacidad(conn):
    _poblar_base(conn)
    metricas = ColectorMetricas(run_id="test", fase="fase4")
    scores = calcular_scores(conn, metricas, ahora=datetime(2026, 9, 15, 12, 0))

    asignaciones = asignar_leads(conn, scores, metricas, fecha=date(2026, 9, 15))

    asesores_usados = {a["asesor_id"] for a in asignaciones}
    assert asesores_usados <= {"AS-01", "AS-02"}  # nunca AS-03 (inactivo)
    assert all(a["empresa_id"] == "EMP-01" for a in asignaciones)
    lead_ids_asignados = [a["lead_id"] for a in asignaciones]
    assert len(lead_ids_asignados) == len(set(lead_ids_asignados))  # nadie asignado 2 veces


def test_asignar_leads_orden_prioridad_arranca_en_1_por_asesor(conn):
    _poblar_base(conn)
    metricas = ColectorMetricas(run_id="test", fase="fase4")
    scores = calcular_scores(conn, metricas, ahora=datetime(2026, 9, 15, 12, 0))

    asignaciones = asignar_leads(conn, scores, metricas, fecha=date(2026, 9, 15))

    por_asesor: dict[str, list[int]] = {}
    for a in asignaciones:
        por_asesor.setdefault(a["asesor_id"], []).append(a["orden_prioridad"])
    for ordenes in por_asesor.values():
        assert sorted(ordenes) == list(range(1, len(ordenes) + 1))


def test_persistir_asignaciones_solo_reemplaza_el_dia_indicado(conn):
    conn.execute(
        schema.asignaciones.insert(),
        [{"lead_id": "LD-AYER", "asesor_id": "AS-01", "empresa_id": "EMP-01",
          "fecha_asignacion": date(2026, 9, 14), "orden_prioridad": 1}],
    )
    conn.commit()

    persistir_asignaciones(
        conn,
        [{"lead_id": "LD-HOY", "asesor_id": "AS-01", "empresa_id": "EMP-01",
          "fecha_asignacion": date(2026, 9, 15), "orden_prioridad": 1}],
        fecha=date(2026, 9, 15),
    )
    conn.commit()

    filas = conn.execute(select(schema.asignaciones)).mappings().all()
    lead_ids = {f["lead_id"] for f in filas}
    assert lead_ids == {"LD-AYER", "LD-HOY"}  # ayer se conserva, hoy se agrego
