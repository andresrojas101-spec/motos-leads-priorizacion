"""Fase 1 - Ingesta y normalización de los archivos fuente.

Estrategia de carga: refresco completo. Cada corrida reconstruye el almacén desde los
archivos crudos, lo que hace el pipeline idempotente y re-ejecutable sin efectos
acumulativos. Con datos incrementales reales esto pasaría a un upsert por llave.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

import pandas as pd
from sqlalchemy import Connection

from src import schema
from src.calidad import ColectorMetricas
from src.config import (
    ARCHIVO_ASESORES,
    ARCHIVO_CATALOGO,
    ARCHIVO_CONVERSACIONES,
    ARCHIVO_HISTORICO,
    ARCHIVO_LEADS,
    NOMBRES_EMPRESAS,
)
from src.normalizadores import (
    ESTADOS_CON_CONTACTO,
    EmparejadorModelos,
    normalizar_canal,
    normalizar_ciudad,
    normalizar_email,
    normalizar_estado_gestion,
    normalizar_nombre,
    normalizar_telefono,
    parsear_fecha,
)

log = logging.getLogger(__name__)


def _texto(valor: object) -> str | None:
    """Convierte NaN/vacío a None, conservando el resto como string limpio."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    texto = str(valor).strip()
    return texto or None


# --------------------------------------------------------------------------------------
# Dimensiones
# --------------------------------------------------------------------------------------


def cargar_dimensiones(conn: Connection, metricas: ColectorMetricas) -> EmparejadorModelos:
    """Carga empresas, puntos de venta, asesores y catálogo.

    Devuelve el emparejador de modelos ya construido con el catálogo, que se reutiliza
    para resolver el texto libre de `modelo_interes_texto` (R6).
    """
    df_asesores = pd.read_csv(ARCHIVO_ASESORES, dtype=str)

    # El mapeo punto_venta -> empresa se deriva de asesores.csv, que la exploración
    # confirmó consistente al 100% con leads.csv e historico_cierres.csv.
    empresas = sorted(df_asesores.empresa_id.unique())
    conn.execute(
        schema.empresas.insert(),
        [{"empresa_id": e, "nombre": NOMBRES_EMPRESAS.get(e, e)} for e in empresas],
    )

    mapa_pv = df_asesores.drop_duplicates("punto_venta_id")[["punto_venta_id", "empresa_id"]]
    conn.execute(schema.puntos_venta.insert(), mapa_pv.to_dict("records"))

    conn.execute(
        schema.asesores.insert(),
        [
            {
                "asesor_id": f.asesor_id,
                "nombre": f.nombre,
                "punto_venta_id": f.punto_venta_id,
                "empresa_id": f.empresa_id,
                "capacidad_diaria_leads": int(f.capacidad_diaria_leads),
                "activo": f.activo.strip().upper() == "SI",
                "fecha_ingreso": (parsear_fecha(f.fecha_ingreso).fecha or datetime.min).date(),
            }
            for f in df_asesores.itertuples()
        ],
    )

    df_catalogo = pd.read_csv(ARCHIVO_CATALOGO)
    filas_catalogo, disponibilidad = [], []
    for f in df_catalogo.itertuples():
        modelo = f"{f.marca} {f.linea}"
        filas_catalogo.append(
            {
                "sku": f.sku,
                "marca": f.marca,
                "linea": f.linea,
                "modelo_normalizado": modelo,
                "cilindraje": int(f.cilindraje),
                "segmento": f.segmento,
                "precio_lista": int(f.precio_lista),
                "unidades_disponibles": int(f.unidades_disponibles),
            }
        )
        # El CSV trae la disponibilidad como 'PV-001|PV-003|...'; se normaliza a filas.
        for pv in str(f.puntos_venta_disponibles).split("|"):
            disponibilidad.append({"sku": f.sku, "punto_venta_id": pv.strip()})

    conn.execute(schema.catalogo_motos.insert(), filas_catalogo)
    conn.execute(schema.catalogo_disponibilidad.insert(), disponibilidad)

    metricas.registrar("dimensiones", "empresas", len(empresas))
    metricas.registrar("dimensiones", "puntos_venta", len(mapa_pv))
    metricas.registrar("dimensiones", "asesores", len(df_asesores))
    metricas.registrar(
        "dimensiones", "asesores_inactivos", int((df_asesores.activo.str.upper() == "NO").sum())
    )
    metricas.registrar("dimensiones", "skus_catalogo", len(filas_catalogo))
    metricas.registrar("dimensiones", "filas_disponibilidad_sku_pv", len(disponibilidad))

    log.info(
        "Dimensiones cargadas: %d empresas, %d puntos de venta, %d asesores, %d SKU",
        len(empresas), len(mapa_pv), len(df_asesores), len(filas_catalogo),
    )
    return EmparejadorModelos(filas_catalogo)


# --------------------------------------------------------------------------------------
# Leads
# --------------------------------------------------------------------------------------


def normalizar_leads(
    emparejador: EmparejadorModelos, metricas: ColectorMetricas
) -> tuple[list[dict], list[dict]]:
    """Aplica R1-R7, R9, R11 y R12 a leads.csv.

    Devuelve (candidatos_validos, rechazados). Los candidatos aún no tienen `persona_id`
    ni `es_lead_canonico`: eso lo resuelve la deduplicación (R8) en src/dedup.py.
    """
    df = pd.read_csv(ARCHIVO_LEADS, dtype=str)
    total_crudo = len(df)

    # R7 - duplicados exactos de fila completa (LD-00011 y LD-00251 vienen dos veces).
    df = df.drop_duplicates()
    duplicados_exactos = total_crudo - len(df)

    candidatos: list[dict] = []
    rechazados: list[dict] = []
    ahora = datetime.now()
    conteo_metodo_modelo: dict[str, int] = {}
    conteo_ciudad: dict[str, int] = {}
    fechas_ambiguas = 0
    ciudades_no_resueltas = 0
    estados_inconsistentes = 0

    for fila in df.to_dict("records"):
        lead_id = _texto(fila.get("lead_id"))
        crudo = {k: _texto(v) for k, v in fila.items()}

        # R1 - sin teléfono marcable no se puede deduplicar ni gestionar.
        telefono = normalizar_telefono(fila.get("telefono"))
        if not telefono.valido:
            rechazados.append(
                {
                    "lead_id_declarado": lead_id,
                    "motivo": telefono.motivo,
                    "detalle": f"telefono={fila.get('telefono')!r}",
                    "payload": crudo,
                    "fecha_ingesta": ahora,
                }
            )
            continue

        # R2 - la fecha de registro es obligatoria: sin ella no hay antigüedad que priorizar.
        registro = parsear_fecha(fila.get("fecha_registro"))
        if registro.fecha is None:
            rechazados.append(
                {
                    "lead_id_declarado": lead_id,
                    "motivo": registro.motivo,
                    "detalle": f"fecha_registro={fila.get('fecha_registro')!r}",
                    "payload": crudo,
                    "fecha_ingesta": ahora,
                }
            )
            continue

        primer_contacto = parsear_fecha(fila.get("fecha_primer_contacto"))
        if registro.confianza == "ambigua" or primer_contacto.confianza == "ambigua":
            fechas_ambiguas += 1

        ciudad = normalizar_ciudad(fila.get("ciudad"))
        conteo_ciudad[ciudad.metodo] = conteo_ciudad.get(ciudad.metodo, 0) + 1
        if ciudad.metodo == "sin_match":
            ciudades_no_resueltas += 1

        modelo = emparejador.emparejar(fila.get("modelo_interes_texto"))
        conteo_metodo_modelo[modelo.metodo] = conteo_metodo_modelo.get(modelo.metodo, 0) + 1

        estado = normalizar_estado_gestion(fila.get("estado_gestion"))
        # R9 - el estado dice que hubo contacto pero no hay timestamp que lo respalde.
        inconsistente = estado in ESTADOS_CON_CONTACTO and primer_contacto.fecha is None
        if inconsistente:
            estados_inconsistentes += 1

        candidatos.append(
            {
                "lead_id": lead_id,
                "empresa_id": _texto(fila.get("empresa_id")),
                "punto_venta_id": _texto(fila.get("punto_venta_id")),
                "canal_origen": normalizar_canal(fila.get("canal")),
                "fecha_registro": registro.fecha,
                "fecha_registro_confianza": registro.confianza,
                "fecha_primer_contacto": primer_contacto.fecha,
                "fecha_primer_contacto_confianza": primer_contacto.confianza,
                "estado_gestion": estado,
                "estado_inconsistente": inconsistente,
                "campania": _texto(fila.get("campania")),
                "ciudad_normalizada": ciudad.ciudad,
                "sku_interes": modelo.sku,
                "marca_interes": modelo.marca,
                "metodo_match_modelo": modelo.metodo,
                "confianza_match_modelo": modelo.confianza,
                "telefono_normalizado": telefono.numero,
                "nombre_raw": _texto(fila.get("nombre_cliente")),
                "nombre_normalizado": normalizar_nombre(fila.get("nombre_cliente")),
                "telefono_raw": _texto(fila.get("telefono")),
                "email_raw": _texto(fila.get("email")),
                "email_normalizado": normalizar_email(fila.get("email")),
                "ciudad_raw": _texto(fila.get("ciudad")),
                "modelo_interes_texto": _texto(fila.get("modelo_interes_texto")),
                "fecha_ingesta": ahora,
            }
        )

    metricas.registrar("leads", "filas_crudas", total_crudo)
    metricas.registrar("leads", "duplicados_exactos_descartados", duplicados_exactos, "R7")
    metricas.registrar("leads", "rechazados", len(rechazados), "R1/R2")
    metricas.registrar("leads", "validos", len(candidatos))
    metricas.registrar("leads", "fechas_ambiguas_dd_mm", fechas_ambiguas, "R2 - se asumió DD/MM")
    metricas.registrar("leads", "ciudades_no_resueltas", ciudades_no_resueltas, "R3")
    metricas.registrar("leads", "estados_inconsistentes", estados_inconsistentes, "R9")
    for metodo, n in sorted(conteo_metodo_modelo.items()):
        metricas.registrar("match_modelo", metodo, n, "R6")
    for metodo, n in sorted(conteo_ciudad.items()):
        metricas.registrar("match_ciudad", metodo, n, "R3")

    log.info(
        "Leads normalizados: %d validos, %d rechazados, %d duplicados exactos",
        len(candidatos), len(rechazados), duplicados_exactos,
    )
    return candidatos, rechazados


def persistir_leads(
    conn: Connection, leads: list[dict], rechazados: list[dict]
) -> None:
    """Escribe leads y rechazados, descartando las columnas auxiliares de trabajo."""
    columnas = set(schema.leads.c.keys())
    conn.execute(
        schema.leads.insert(),
        [{k: v for k, v in lead.items() if k in columnas} for lead in leads],
    )
    if rechazados:
        conn.execute(schema.leads_rechazados.insert(), rechazados)


# --------------------------------------------------------------------------------------
# Conversaciones
# --------------------------------------------------------------------------------------


def leer_lead_ids_con_conversacion() -> set[str]:
    """Lead IDs declarados en conversaciones.json.

    Se lee antes de deduplicar porque R8 prefiere como lead canónico al que tiene
    conversación asociada: es el que trae más contexto para el asesor.
    """
    with open(ARCHIVO_CONVERSACIONES, encoding="utf-8") as f:
        return {c["lead_id"] for c in json.load(f)}


def cargar_conversaciones(
    conn: Connection, empresa_por_lead: dict[str, str], metricas: ColectorMetricas
) -> None:
    """R10 - Carga conversaciones y mensajes, marcando las huérfanas.

    Las conversaciones cuyo lead_id no existe en leads se conservan con
    estado_vinculacion='HUERFANA' y lead_id nulo: no se inventan leads ni se borra
    evidencia, pero quedan excluidas del enriquecimiento con IA de la Fase 2.
    """
    with open(ARCHIVO_CONVERSACIONES, encoding="utf-8") as f:
        crudas = json.load(f)

    filas_conv, filas_msg = [], []
    huerfanas = 0
    canal_discrepante = 0

    for conv in crudas:
        declarado = conv["lead_id"]
        vinculada = declarado in empresa_por_lead
        if not vinculada:
            huerfanas += 1

        filas_conv.append(
            {
                "conversacion_id": conv["conversacion_id"],
                "lead_id": declarado if vinculada else None,
                "lead_id_declarado": declarado,
                "empresa_id": empresa_por_lead.get(declarado),
                "canal": normalizar_canal(conv.get("canal")),
                "fecha_inicio": parsear_fecha(conv.get("fecha_inicio")).fecha,
                "num_mensajes": len(conv.get("mensajes", [])),
                "estado_vinculacion": "VINCULADA" if vinculada else "HUERFANA",
            }
        )

        for orden, msg in enumerate(conv.get("mensajes", []), start=1):
            filas_msg.append(
                {
                    "conversacion_id": conv["conversacion_id"],
                    "orden": orden,
                    "emisor": msg.get("emisor"),
                    "hora": msg.get("hora"),
                    "texto": msg.get("texto", ""),
                }
            )

    conn.execute(schema.conversaciones.insert(), filas_conv)
    conn.execute(schema.mensajes.insert(), filas_msg)

    # Una misma persona puede tener varias conversaciones: 25 lead_id se repiten.
    leads_unicos = len({c["lead_id_declarado"] for c in filas_conv})

    metricas.registrar("conversaciones", "total", len(filas_conv))
    metricas.registrar("conversaciones", "huerfanas", huerfanas, "R10 - lead_id inexistente")
    metricas.registrar("conversaciones", "vinculadas", len(filas_conv) - huerfanas)
    metricas.registrar("conversaciones", "lead_ids_distintos", leads_unicos)
    metricas.registrar("conversaciones", "mensajes", len(filas_msg))

    log.info(
        "Conversaciones cargadas: %d (%d huerfanas), %d mensajes",
        len(filas_conv), huerfanas, len(filas_msg),
    )


# --------------------------------------------------------------------------------------
# Histórico (cohorte de calibración)
# --------------------------------------------------------------------------------------


def cargar_historico(
    conn: Connection, emparejador: EmparejadorModelos, metricas: ColectorMetricas
) -> None:
    """Carga historico_cierres.csv, que la exploración confirmó limpio y consistente.

    No se relaciona con `leads`: usa namespace HX-* frente a LD-*, con intersección vacía.
    Es la cohorte que permitirá validar el scoring en la Fase 3.
    """
    df = pd.read_csv(ARCHIVO_HISTORICO)

    filas = [
        {
            "lead_id": f.lead_id,
            "fecha_registro": (parsear_fecha(f.fecha_registro).fecha or datetime.min).date(),
            "canal": normalizar_canal(f.canal),
            "empresa_id": f.empresa_id,
            "punto_venta_id": f.punto_venta_id,
            "modelo_cotizado": f.modelo_cotizado,
            "sku": emparejador.emparejar(f.modelo_cotizado).sku,
            "precio_lista": int(f.precio_lista),
            "horas_al_primer_contacto": (
                None if pd.isna(f.horas_al_primer_contacto) else float(f.horas_al_primer_contacto)
            ),
            "numero_contactos": int(f.numero_contactos),
            "manifesto_cuota_inicial": f.manifesto_cuota_inicial,
            "forma_pago_declarada": f.forma_pago_declarada,
            "pidio_cita": str(f.pidio_cita).strip().upper() == "SI",
            "desenlace": f.desenlace,
        }
        for f in df.itertuples()
    ]
    conn.execute(schema.historico_cierres.insert(), filas)

    sin_sku = sum(1 for f in filas if f["sku"] is None)
    cerrados = sum(1 for f in filas if f["desenlace"] == "Cerrado")
    gestionados = sum(1 for f in filas if f["desenlace"] != "Sin gestión")

    metricas.registrar("historico", "filas", len(filas))
    metricas.registrar("historico", "modelos_sin_sku", sin_sku)
    metricas.registrar("historico", "cerrados", cerrados)
    metricas.registrar(
        "historico",
        "tasa_cierre_sobre_gestionados_pct",
        round(100 * cerrados / gestionados, 2) if gestionados else 0,
        "linea base de negocio",
    )

    log.info("Historico cargado: %d filas, %d cerrados", len(filas), cerrados)
