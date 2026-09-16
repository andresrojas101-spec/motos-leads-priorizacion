"""Esquema de la base de datos (SQLAlchemy Core).

Fuente única de verdad del modelo de datos. `python -m src.schema` regenera
`db/schema.sql` con el DDL en dialecto PostgreSQL, versionado en el repositorio.

El diseño y su justificación están en docs/modelo-datos.md.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()

# --------------------------------------------------------------------------------------
# Dimensiones
# --------------------------------------------------------------------------------------

empresas = Table(
    "empresas",
    metadata,
    Column("empresa_id", String(10), primary_key=True),
    Column("nombre", String(120), nullable=False),
)

puntos_venta = Table(
    "puntos_venta",
    metadata,
    Column("punto_venta_id", String(10), primary_key=True),
    Column("empresa_id", String(10), ForeignKey("empresas.empresa_id"), nullable=False),
)

asesores = Table(
    "asesores",
    metadata,
    Column("asesor_id", String(10), primary_key=True),
    Column("nombre", String(120), nullable=False),
    Column("punto_venta_id", String(10), ForeignKey("puntos_venta.punto_venta_id"), nullable=False),
    Column("empresa_id", String(10), ForeignKey("empresas.empresa_id"), nullable=False),
    Column("capacidad_diaria_leads", Integer, nullable=False),
    Column("activo", Boolean, nullable=False),
    Column("fecha_ingreso", Date),
)

catalogo_motos = Table(
    "catalogo_motos",
    metadata,
    Column("sku", String(10), primary_key=True),
    Column("marca", String(40), nullable=False),
    Column("linea", String(60), nullable=False),
    # marca + linea normalizado; es la llave contra la que se hace el matching de R6.
    Column("modelo_normalizado", String(100), nullable=False, index=True),
    Column("cilindraje", Integer),
    Column("segmento", String(40)),
    Column("precio_lista", Integer),
    Column("unidades_disponibles", Integer),
)

# Normaliza el campo `puntos_venta_disponibles` que en el CSV viene como texto con "|".
catalogo_disponibilidad = Table(
    "catalogo_disponibilidad",
    metadata,
    Column("sku", String(10), ForeignKey("catalogo_motos.sku"), primary_key=True),
    Column("punto_venta_id", String(10), ForeignKey("puntos_venta.punto_venta_id"), primary_key=True),
)

# --------------------------------------------------------------------------------------
# Núcleo operativo
# --------------------------------------------------------------------------------------

# Identidad de cliente. La llave natural incluye empresa_id a propósito: 65% de los
# teléfonos duplicados cruzan empresas y fusionarlos violaría la separación por empresa
# (requisito obligatorio #8). Ver regla R8.
personas = Table(
    "personas",
    metadata,
    Column("persona_id", Integer, primary_key=True, autoincrement=True),
    Column("empresa_id", String(10), ForeignKey("empresas.empresa_id"), nullable=False, index=True),
    Column("telefono_normalizado", String(10), nullable=False),
    Column("nombre_canonico", String(120)),
    Column("email_canonico", String(150)),
    Column("ciudad_normalizada", String(60)),
    Column("total_leads", Integer, nullable=False, default=1),
    Column("primer_registro", DateTime),
    Column("ultimo_registro", DateTime),
    UniqueConstraint("empresa_id", "telefono_normalizado", name="uq_persona_empresa_telefono"),
)

leads = Table(
    "leads",
    metadata,
    Column("lead_id", String(20), primary_key=True),
    Column("persona_id", Integer, ForeignKey("personas.persona_id"), nullable=False, index=True),
    Column("empresa_id", String(10), ForeignKey("empresas.empresa_id"), nullable=False, index=True),
    Column("punto_venta_id", String(10), ForeignKey("puntos_venta.punto_venta_id"), nullable=False),
    # Canal por el que ENTRÓ el lead. Distinto del canal donde se conversó (ver R10).
    Column("canal_origen", String(20), nullable=False),
    Column("fecha_registro", DateTime),
    # 'exacta' | 'ambigua' -> ~480 fechas con "/" no permiten distinguir DD/MM de MM/DD (R2).
    Column("fecha_registro_confianza", String(10)),
    Column("fecha_primer_contacto", DateTime),
    Column("fecha_primer_contacto_confianza", String(10)),
    Column("estado_gestion", String(25), nullable=False),
    # True cuando el estado dice "gestionado" pero no hay fecha de primer contacto (R9).
    Column("estado_inconsistente", Boolean, nullable=False, default=False),
    Column("campania", String(80)),
    Column("ciudad_normalizada", String(60)),
    Column("sku_interes", String(10), ForeignKey("catalogo_motos.sku")),
    Column("marca_interes", String(40)),
    # exacto | solo_marca | contencion_unica | fuzzy | ambiguo | sin_match (R6)
    Column("metodo_match_modelo", String(25)),
    Column("confianza_match_modelo", Float),
    # False para los leads secundarios de una persona deduplicada (R8).
    Column("es_lead_canonico", Boolean, nullable=False, default=True),
    # Valores crudos, preservados para auditoría y reprocesamiento.
    Column("nombre_raw", String(120)),
    Column("telefono_raw", String(40)),
    Column("email_raw", String(150)),
    Column("ciudad_raw", String(60)),
    Column("modelo_interes_texto", String(120)),
    Column("fecha_ingesta", DateTime),
)

leads_rechazados = Table(
    "leads_rechazados",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lead_id_declarado", String(20)),
    Column("motivo", String(40), nullable=False),
    Column("detalle", Text),
    Column("payload", JSON),
    Column("fecha_ingesta", DateTime),
)

# --------------------------------------------------------------------------------------
# Conversaciones
# --------------------------------------------------------------------------------------

conversaciones = Table(
    "conversaciones",
    metadata,
    Column("conversacion_id", String(20), primary_key=True),
    # NULL cuando la conversación es huérfana (su lead_id no existe en leads).
    Column("lead_id", String(20), ForeignKey("leads.lead_id"), index=True),
    Column("lead_id_declarado", String(20), nullable=False),
    Column("empresa_id", String(10), ForeignKey("empresas.empresa_id"), index=True),
    Column("canal", String(20)),
    Column("fecha_inicio", DateTime),
    Column("num_mensajes", Integer),
    # VINCULADA | HUERFANA (R10)
    Column("estado_vinculacion", String(15), nullable=False),
)

mensajes = Table(
    "mensajes",
    metadata,
    Column("mensaje_id", Integer, primary_key=True, autoincrement=True),
    Column("conversacion_id", String(20), ForeignKey("conversaciones.conversacion_id"), nullable=False, index=True),
    Column("orden", Integer, nullable=False),
    Column("emisor", String(10), nullable=False),
    Column("hora", String(5)),
    Column("texto", Text, nullable=False),
)

# --------------------------------------------------------------------------------------
# Cohorte histórica (calibración del scoring). Sin relación con `leads`: otro namespace
# de IDs (HX-* vs LD-*), intersección vacía.
# --------------------------------------------------------------------------------------

historico_cierres = Table(
    "historico_cierres",
    metadata,
    Column("lead_id", String(20), primary_key=True),
    Column("fecha_registro", Date),
    Column("canal", String(20)),
    Column("empresa_id", String(10)),
    Column("punto_venta_id", String(10)),
    Column("modelo_cotizado", String(100)),
    Column("sku", String(10), ForeignKey("catalogo_motos.sku")),
    Column("precio_lista", Integer),
    Column("horas_al_primer_contacto", Float),
    Column("numero_contactos", Integer),
    Column("manifesto_cuota_inicial", String(15)),
    Column("forma_pago_declarada", String(15)),
    Column("pidio_cita", Boolean),
    Column("desenlace", String(15)),
)

# --------------------------------------------------------------------------------------
# Fases 2-4: se crean ahora (esquema completo versionado) y se pueblan más adelante.
# --------------------------------------------------------------------------------------

enriquecimiento_conversacion = Table(
    "enriquecimiento_conversacion",
    metadata,
    Column("conversacion_id", String(20), ForeignKey("conversaciones.conversacion_id"), primary_key=True),
    Column("lead_id", String(20), ForeignKey("leads.lead_id"), index=True),
    Column("modelo_interes_texto", String(120)),
    Column("sku_interes", String(10), ForeignKey("catalogo_motos.sku")),
    Column("presupuesto_monto", Integer),
    Column("forma_pago", String(20)),
    Column("intencion", String(20)),
    Column("objecion_principal", String(40)),
    Column("pidio_cita", Boolean),
    Column("pidio_cotizacion", Boolean),
    Column("confianza_global", Float),
    # OK | REINTENTO_OK | FALLO -> una falla degrada el lead, no bloquea el pipeline.
    Column("extraccion_status", String(15), nullable=False),
    Column("modelo_llm", String(40)),
    Column("fecha_extraccion", DateTime),
)

lead_scores = Table(
    "lead_scores",
    metadata,
    Column("lead_id", String(20), ForeignKey("leads.lead_id"), primary_key=True),
    Column("empresa_id", String(10), ForeignKey("empresas.empresa_id"), nullable=False, index=True),
    Column("score_total", Float, nullable=False),
    Column("temperatura", String(10), nullable=False),
    # Aporte de cada componente del scorecard: hace auditable el "por qué" de la prioridad.
    Column("desglose", JSON),
    Column("version_scoring", String(20), nullable=False),
    Column("fecha_calculo", DateTime),
)

asignaciones = Table(
    "asignaciones",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("lead_id", String(20), ForeignKey("leads.lead_id"), nullable=False, index=True),
    Column("asesor_id", String(10), ForeignKey("asesores.asesor_id"), nullable=False, index=True),
    Column("empresa_id", String(10), ForeignKey("empresas.empresa_id"), nullable=False),
    Column("fecha_asignacion", Date, nullable=False),
    Column("orden_prioridad", Integer, nullable=False),
)

# --------------------------------------------------------------------------------------
# Auditoría del pipeline
# --------------------------------------------------------------------------------------

ejecuciones = Table(
    "ejecuciones",
    metadata,
    Column("run_id", String(40), primary_key=True),
    Column("fase", String(20), nullable=False),
    Column("inicio", DateTime, nullable=False),
    Column("fin", DateTime),
    Column("estado", String(15), nullable=False),
)

metricas_calidad = Table(
    "metricas_calidad",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String(40), ForeignKey("ejecuciones.run_id"), nullable=False, index=True),
    Column("categoria", String(40), nullable=False),
    Column("metrica", String(80), nullable=False),
    Column("valor", Float),
    Column("detalle", Text),
)


def emitir_ddl_postgres() -> str:
    """Genera el DDL completo en dialecto PostgreSQL."""
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateIndex, CreateTable

    dialecto = postgresql.dialect()
    partes = [
        "-- Generado por `python -m src.schema`. No editar a mano.",
        "-- Modelo de datos y justificación: docs/modelo-datos.md",
        "",
    ]
    for tabla in metadata.sorted_tables:
        partes.append(str(CreateTable(tabla).compile(dialect=dialecto)).strip() + ";")
        for indice in tabla.indexes:
            partes.append(str(CreateIndex(indice).compile(dialect=dialecto)).strip() + ";")
        partes.append("")
    return "\n".join(partes)


if __name__ == "__main__":
    from src.config import DIR_DB

    DIR_DB.mkdir(parents=True, exist_ok=True)
    destino = DIR_DB / "schema.sql"
    destino.write_text(emitir_ddl_postgres(), encoding="utf-8")
    print(f"DDL escrito en {destino} ({len(metadata.tables)} tablas)")
