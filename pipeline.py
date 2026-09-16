"""Punto de entrada del pipeline.

Un solo disparo ejecuta el flujo completo, sin pasos manuales:

    python pipeline.py --fase 1              # ingesta + normalizacion + deduplicacion
    python pipeline.py --fase 2               # extraccion con IA desde conversaciones
    python pipeline.py --fase 2 --limite 20   # prueba de costo/calidad sobre una muestra
    python pipeline.py                        # todas las fases disponibles
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime

from sqlalchemy import create_engine

from src import schema
from src.calidad import ColectorMetricas
from src.config import ANTHROPIC_API_KEY, DATABASE_URL, MODELO_LLM, configurar_logging
from src.dedup import construir_personas, persistir_personas
from src.extraccion import ejecutar_extraccion
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
    # Se reconstruyen solo las tablas de datos; el historial de auditoria se conserva.
    schema.metadata.drop_all(engine, tables=schema.tablas_de_datos())
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


def ejecutar_fase2(limite: int | None = None) -> ColectorMetricas:
    """Fase 2 - Extraccion con IA desde conversaciones.

    Requiere que la Fase 1 ya haya poblado `conversaciones` y `catalogo_motos`. Es
    reanudable: solo procesa conversaciones que aun no tengan fila en
    `enriquecimiento_conversacion`, asi que una corrida interrumpida se completa
    volviendo a lanzar el mismo comando.
    """
    if not ANTHROPIC_API_KEY:
        raise SystemExit(
            "ANTHROPIC_API_KEY no esta configurada. Copia .env.example a .env y "
            "completa la llave antes de correr la Fase 2."
        )

    from src.llm_cliente import ClienteAnthropic

    run_id = f"fase2-{datetime.now():%Y%m%d-%H%M%S}"
    metricas = ColectorMetricas(run_id=run_id, fase="fase2")
    engine = create_engine(DATABASE_URL)
    cliente = ClienteAnthropic(api_key=ANTHROPIC_API_KEY, modelo=MODELO_LLM)

    log.info("Iniciando %s con modelo %s%s", run_id, MODELO_LLM, f" (limite={limite})" if limite else "")

    with engine.begin() as conn:
        ejecutar_extraccion(conn, cliente, metricas, limite=limite)
        metricas.persistir(conn)

    log.info("Fase 2 completada")
    return metricas


FASES = {1: ejecutar_fase1, 2: ejecutar_fase2}


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline de priorizacion de leads")
    parser.add_argument(
        "--fase",
        type=int,
        choices=sorted(FASES),
        help="Ejecuta solo una fase. Por defecto corre todas las disponibles.",
    )
    parser.add_argument("--silencioso", action="store_true", help="Omite el reporte de calidad")
    parser.add_argument(
        "--limite",
        type=int,
        default=None,
        help="Solo Fase 2: procesa a lo sumo N conversaciones (prueba de costo/calidad).",
    )
    args = parser.parse_args()

    configurar_logging()
    fases = [args.fase] if args.fase else sorted(FASES)

    for numero in fases:
        metricas = FASES[numero](limite=args.limite) if numero == 2 else FASES[numero]()
        if not args.silencioso:
            metricas.imprimir_resumen()


if __name__ == "__main__":
    main()
