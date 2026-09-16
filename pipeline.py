"""Punto de entrada del pipeline.

Un solo disparo ejecuta el flujo completo, sin pasos manuales:

    python pipeline.py --fase 1     # ingesta + normalizacion + deduplicacion
    python pipeline.py              # todas las fases disponibles
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime

from sqlalchemy import create_engine

from src import schema
from src.calidad import ColectorMetricas
from src.config import DATABASE_URL, configurar_logging
from src.dedup import construir_personas, persistir_personas
from src.ingesta import (
    cargar_conversaciones,
    cargar_dimensiones,
    cargar_historico,
    leer_lead_ids_con_conversacion,
    normalizar_leads,
    persistir_leads,
)

log = logging.getLogger("pipeline")


def ejecutar_fase1() -> ColectorMetricas:
    """Fase 1 - Ingesta, normalizacion y deduplicacion.

    Refresco completo: se reconstruye el almacen desde los archivos crudos en cada
    corrida, lo que hace el pipeline idempotente.
    """
    run_id = f"fase1-{datetime.now():%Y%m%d-%H%M%S}"
    metricas = ColectorMetricas(run_id=run_id, fase="fase1")
    engine = create_engine(DATABASE_URL)

    log.info("Iniciando %s sobre %s", run_id, engine.url.render_as_string(hide_password=True))
    schema.metadata.drop_all(engine)
    schema.metadata.create_all(engine)

    with engine.begin() as conn:
        emparejador = cargar_dimensiones(conn, metricas)

        candidatos, rechazados = normalizar_leads(emparejador, metricas)

        # Se lee antes de deduplicar: R8 prefiere como canonico al lead con conversacion.
        con_conversacion = leer_lead_ids_con_conversacion()
        personas = construir_personas(candidatos, con_conversacion, metricas)

        persistir_personas(conn, personas)
        persistir_leads(conn, candidatos, rechazados)

        empresa_por_lead = {lead["lead_id"]: lead["empresa_id"] for lead in candidatos}
        cargar_conversaciones(conn, empresa_por_lead, metricas)

        cargar_historico(conn, emparejador, metricas)

        metricas.persistir(conn)

    log.info("Fase 1 completada")
    return metricas


FASES = {1: ejecutar_fase1}


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline de priorizacion de leads")
    parser.add_argument(
        "--fase",
        type=int,
        choices=sorted(FASES),
        help="Ejecuta solo una fase. Por defecto corre todas las disponibles.",
    )
    parser.add_argument("--silencioso", action="store_true", help="Omite el reporte de calidad")
    args = parser.parse_args()

    configurar_logging()
    fases = [args.fase] if args.fase else sorted(FASES)

    for numero in fases:
        metricas = FASES[numero]()
        if not args.silencioso:
            metricas.imprimir_resumen()


if __name__ == "__main__":
    main()
