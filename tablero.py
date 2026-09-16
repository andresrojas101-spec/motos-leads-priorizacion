"""Fase 6 - Tablero publico: la lista priorizada de gestion diaria por asesor.

Lee directo de la base de datos (Postgres/Supabase en produccion, SQLite si se corre
local) -- no hay snapshot ni exportacion intermedia. `st.cache_data` con TTL evita
golpear la base en cada clic del usuario, no reemplaza la fuente de verdad.

Simplificacion deliberada de la demo: el limite entre comercializadoras (requisito
obligatorio del enunciado) esta garantizado en los DATOS -- toda query aqui filtra por
`empresa_id`, y la Fase 4 ya verifico 0 cruces reales entre asesores y leads de otra
empresa. Lo que NO hay es autenticacion: cualquiera con el link puede cambiar el
selector de empresa. En produccion real esto se resolveria con login (Supabase Auth +
Row Level Security). Decision explicita para priorizar terminar las 7 fases en el
plazo, documentada en CLAUDE.md.
"""

from __future__ import annotations

import contextlib
import json
import os

import pandas as pd
import streamlit as st
from sqlalchemy import bindparam, text

with contextlib.suppress(Exception):
    _db_secret = st.secrets.get("DATABASE_URL")
    if _db_secret:
        os.environ["DATABASE_URL"] = _db_secret

from src.config import DATABASE_URL  # noqa: E402  (debe importarse tras fijar el secret)

st.set_page_config(page_title="Priorizacion de leads", page_icon="🏍️", layout="wide")


@st.cache_resource
def _engine():
    from sqlalchemy import create_engine

    return create_engine(DATABASE_URL)


@st.cache_data(ttl=300)
def cargar_empresas() -> pd.DataFrame:
    return pd.read_sql(text("SELECT empresa_id, nombre FROM empresas ORDER BY empresa_id"), _engine())


@st.cache_data(ttl=300)
def cargar_kpis(empresa_id: str) -> dict:
    engine = _engine()
    temp = pd.read_sql(
        text("SELECT temperatura, COUNT(*) AS n FROM lead_scores WHERE empresa_id=:e GROUP BY temperatura"),
        engine,
        params={"e": empresa_id},
    ).set_index("temperatura")["n"].to_dict()
    total = int(
        pd.read_sql(
            text("SELECT COUNT(*) AS n FROM lead_scores WHERE empresa_id=:e"), engine, params={"e": empresa_id}
        )["n"].iloc[0]
    )
    fecha = pd.read_sql(
        text("SELECT MAX(fecha_asignacion) AS f FROM asignaciones WHERE empresa_id=:e"),
        engine,
        params={"e": empresa_id},
    )["f"].iloc[0]
    asignados = 0
    if fecha is not None:
        asignados = int(
            pd.read_sql(
                text("SELECT COUNT(*) AS n FROM asignaciones WHERE empresa_id=:e AND fecha_asignacion=:f"),
                engine,
                params={"e": empresa_id, "f": fecha},
            )["n"].iloc[0]
        )
    return {
        "caliente": int(temp.get("CALIENTE", 0)),
        "tibio": int(temp.get("TIBIO", 0)),
        "frio": int(temp.get("FRIO", 0)),
        "total": total,
        "asignados": asignados,
        "sin_capacidad": total - asignados,
        "fecha": fecha,
    }


@st.cache_data(ttl=300)
def cargar_bandeja(empresa_id: str) -> pd.DataFrame:
    """La bandeja de gestion diaria: leads ya asignados a un asesor en la corrida mas
    reciente, con el desglose de score y el contexto extraido de la conversacion."""
    engine = _engine()
    fecha = pd.read_sql(
        text("SELECT MAX(fecha_asignacion) AS f FROM asignaciones WHERE empresa_id=:e"),
        engine,
        params={"e": empresa_id},
    )["f"].iloc[0]
    if fecha is None:
        return pd.DataFrame()

    asignados = pd.read_sql(
        text(
            """
            SELECT a.orden_prioridad, a.asesor_id, ase.nombre AS asesor_nombre,
                   a.lead_id, l.punto_venta_id, l.nombre_raw, l.telefono_raw,
                   l.ciudad_normalizada, l.canal_origen, l.estado_gestion,
                   ls.score_total, ls.temperatura, ls.desglose
            FROM asignaciones a
            JOIN leads l ON l.lead_id = a.lead_id
            JOIN asesores ase ON ase.asesor_id = a.asesor_id
            JOIN lead_scores ls ON ls.lead_id = a.lead_id
            WHERE a.empresa_id = :e AND a.fecha_asignacion = :f
            """
        ),
        engine,
        params={"e": empresa_id, "f": fecha},
    )

    # Un lead puede tener mas de una conversacion vinculada (ver fix de Fase 4 en
    # CLAUDE.md) -- mismo criterio aqui: la mas reciente por fecha_inicio.
    if asignados.empty:
        enriquecimiento = pd.DataFrame()
    else:
        consulta_enriquecimiento = text(
            """
            SELECT e.lead_id, e.modelo_interes_texto, e.forma_pago, e.intencion,
                   e.objecion_principal, e.pidio_cita, e.pidio_cotizacion,
                   e.confianza_global, c.fecha_inicio
            FROM enriquecimiento_conversacion e
            JOIN conversaciones c ON c.conversacion_id = e.conversacion_id
            WHERE e.extraccion_status IN ('OK', 'REINTENTO_OK')
              AND e.lead_id IN :leads
            """
        ).bindparams(bindparam("leads", expanding=True))
        enriquecimiento = pd.read_sql(
            consulta_enriquecimiento, engine, params={"leads": tuple(asignados["lead_id"])}
        )

    if not enriquecimiento.empty:
        enriquecimiento = enriquecimiento.sort_values("fecha_inicio").drop_duplicates("lead_id", keep="last")
        asignados = asignados.merge(enriquecimiento.drop(columns="fecha_inicio"), on="lead_id", how="left")

    return asignados.sort_values(["asesor_nombre", "orden_prioridad"]).reset_index(drop=True)


def _emoji_temperatura(t: str) -> str:
    return {"CALIENTE": "🔥", "TIBIO": "🟡", "FRIO": "🧊"}.get(t, t)


# --------------------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------------------

st.title("🏍️ Priorización de leads")
st.caption(
    "Lista de gestión diaria por asesor, calculada por el pipeline automatizado. "
    "Selector de empresa como simplificación de la demo (sin login) — ver CLAUDE.md."
)

empresas = cargar_empresas()
if empresas.empty:
    st.error("No hay empresas en la base de datos. ¿Ya corrió la Fase 1 del pipeline?")
    st.stop()

nombre_a_id = dict(zip(empresas["nombre"], empresas["empresa_id"]))
empresa_nombre = st.selectbox("Empresa", options=list(nombre_a_id))
empresa_id = nombre_a_id[empresa_nombre]

kpis = cargar_kpis(empresa_id)
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("🔥 Caliente", kpis["caliente"])
col2.metric("🟡 Tibio", kpis["tibio"])
col3.metric("🧊 Frío", kpis["frio"])
col4.metric("Asignados hoy", kpis["asignados"])
col5.metric("Sin capacidad hoy", kpis["sin_capacidad"], help="Exceden la capacidad diaria de su punto de venta; quedan para la siguiente corrida.")

if kpis["fecha"] is not None:
    st.caption(f"Última corrida de asignación: {kpis['fecha']}")

bandeja = cargar_bandeja(empresa_id)
if bandeja.empty:
    st.info("Todavía no hay una corrida de asignación para esta empresa.")
    st.stop()

st.divider()

col_a, col_b = st.columns(2)
asesor_sel = col_a.selectbox("Asesor", ["Todos"] + sorted(bandeja["asesor_nombre"].unique().tolist()))
temp_sel = col_b.selectbox("Temperatura", ["Todas", "CALIENTE", "TIBIO", "FRIO"])

vista = bandeja.copy()
if asesor_sel != "Todos":
    vista = vista[vista["asesor_nombre"] == asesor_sel]
if temp_sel != "Todas":
    vista = vista[vista["temperatura"] == temp_sel]

vista_mostrar = vista.copy()
vista_mostrar["temperatura"] = vista_mostrar["temperatura"].map(_emoji_temperatura)
vista_mostrar["score_total"] = vista_mostrar["score_total"].round(1)

st.dataframe(
    vista_mostrar[
        [
            "orden_prioridad", "asesor_nombre", "lead_id", "nombre_raw", "telefono_raw",
            "temperatura", "score_total", "modelo_interes_texto", "forma_pago",
            "intencion", "objecion_principal", "pidio_cita", "estado_gestion",
        ]
    ].rename(
        columns={
            "orden_prioridad": "#", "asesor_nombre": "Asesor", "lead_id": "Lead",
            "nombre_raw": "Cliente", "telefono_raw": "Teléfono", "temperatura": "Temp.",
            "score_total": "Score", "modelo_interes_texto": "Modelo interés",
            "forma_pago": "Forma pago", "intencion": "Intención",
            "objecion_principal": "Objeción", "pidio_cita": "¿Pidió cita?",
            "estado_gestion": "Estado",
        }
    ),
    width="stretch",
    hide_index=True,
)

st.divider()
st.subheader("¿Por qué este score?")
st.caption("Explicabilidad del scorecard (Fase 3): aporte de cada componente ponderado.")

lead_sel = st.selectbox("Lead", vista["lead_id"].tolist())
fila = vista[vista["lead_id"] == lead_sel].iloc[0]
desglose_raw = fila["desglose"]
desglose = json.loads(desglose_raw) if isinstance(desglose_raw, str) else desglose_raw

componentes = desglose.get("componentes", [])
if componentes:
    df_comp = pd.DataFrame(componentes)
    df_comp["aporte"] = df_comp["peso"] * df_comp["sub_score"]
    st.bar_chart(df_comp.set_index("nombre")[["aporte"]])
    st.dataframe(
        df_comp.rename(
            columns={"nombre": "Componente", "peso": "Peso", "sub_score": "Sub-score (0-100)", "aporte": "Aporte al score"}
        ).round(1),
        width="stretch",
        hide_index=True,
    )
else:
    st.write("Sin desglose disponible para este lead.")
