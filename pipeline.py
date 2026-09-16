"""Punto de entrada del pipeline.

Un solo disparo ejecuta el flujo completo, sin pasos manuales:

    python pipeline.py --fase 1              # ingesta + normalizacion + deduplicacion
    python pipeline.py --fase 2               # extraccion con IA desde conversaciones
    python pipeline.py --fase 2 --limite 20   # prueba de costo/calidad sobre una muestra
    python pipeline.py --fase 4               # scoring + asignacion a asesores
    python pipeline.py                        # todas las fases disponibles
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime

from sqlalchemy import create_engine

from src import schema
from src.asignacion import (
    asignar_leads,
    calcular_scores,
    persistir_asignaciones,
    persistir_scores,
)
from src.calidad import ColectorMetricas
from src.config import DATABASE_URL, MODELO_LLM, PROVEEDOR_LLM, configurar_logging
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
    schema.metadata.create_all(engine)

    with engine.begin() as conn:
        # Se vacian solo las tablas de datos (el historial de auditoria se conserva), con
        # DELETE en vez de DROP TABLE: `enriquecimiento_conversacion`/`lead_scores`/
        # `asignaciones` tienen FK hacia estas tablas y sobreviven al refresco (sus FK son
        # deferrable, ver schema.py) porque los ids se reinsertan identicos mas abajo --
        # DROP TABLE rompia esa referencia en Postgres (FK no diferible a nivel de DDL).
        for tabla in schema.tablas_de_datos():
            conn.execute(tabla.delete())

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
    from src.llm_cliente import construir_cliente_llm

    try:
        cliente = construir_cliente_llm()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    run_id = f"fase2-{datetime.now():%Y%m%d-%H%M%S}"
    metricas = ColectorMetricas(run_id=run_id, fase="fase2")
    engine = create_engine(DATABASE_URL)

    log.info(
        "Iniciando %s con proveedor=%s modelo=%s%s",
        run_id, PROVEEDOR_LLM, MODELO_LLM, f" (limite={limite})" if limite else "",
    )

    # engine.connect() (no .begin()): ejecutar_extraccion hace sus propios commits
    # incrementales por lote. Con .begin() todo quedaria atrapado en una unica
    # transaccion de horas, sin durabilidad real hasta el final -- justo el problema que
    # la persistencia incremental busca evitar. Ver docstring de ejecutar_extraccion.
    with engine.connect() as conn:
        ejecutar_extraccion(conn, cliente, metricas, limite=limite)
        metricas.persistir(conn)
        conn.commit()

    log.info("Fase 2 completada")
    return metricas


def ejecutar_fase4(fecha_referencia: datetime | None = None) -> ColectorMetricas:
    """Fase 4 - Scoring (Fase 3) + asignacion a asesores.

    Requiere que la Fase 1 haya corrido (leads/asesores) y, opcionalmente, la Fase 2
    (leads sin enriquecimiento se scorean solo con urgencia — ver docs/scoring.md).

    `fecha_referencia` fija el "ahora" contra el que se mide `horas_sin_avance`. Por
    defecto es el momento real de la corrida (uso normal en producción, disparado a
    diario). Se puede fijar a una fecha distinta para reconstruir cómo se habría visto
    la bandeja de un asesor en un día concreto, o para correr una demo sobre datos
    sintéticos ya congelados sin que todo salga FRÍO solo por la distancia entre la
    fecha de esos leads y la fecha real de hoy.

    Refresco completo de `lead_scores` en cada corrida: el score depende de cuanto
    tiempo ha pasado desde el ultimo avance de cada lead, asi que es intrinsecamente un
    calculo "para ahora", no algo que tenga sentido acumular historicamente fila a fila.
    `asignaciones` en cambio solo reemplaza el dia de hoy, conservando el historial de
    bandejas anteriores.
    """
    ahora = fecha_referencia or datetime.now()
    run_id = f"fase4-{datetime.now():%Y%m%d-%H%M%S}"
    metricas = ColectorMetricas(run_id=run_id, fase="fase4")
    engine = create_engine(DATABASE_URL)

    log.info("Iniciando %s (fecha_referencia=%s)", run_id, ahora.isoformat())

    with engine.begin() as conn:
        scores = calcular_scores(conn, metricas, ahora=ahora)
        persistir_scores(conn, scores)

        dia = ahora.date()
        asignaciones = asignar_leads(conn, scores, metricas, fecha=dia)
        persistir_asignaciones(conn, asignaciones, dia)

        metricas.persistir(conn)

    log.info("Fase 4 completada")
    return metricas


FASES = {1: ejecutar_fase1, 2: ejecutar_fase2, 4: ejecutar_fase4}


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
    parser.add_argument(
        "--fecha-referencia",
        type=str,
        default=None,
        metavar="YYYY-MM-DD[THH:MM]",
        help="Solo Fase 4: 'ahora' contra el que se mide urgencia. Por defecto, el momento real.",
    )
    args = parser.parse_args()

    configurar_logging()
    fases = [args.fase] if args.fase else sorted(FASES)
    fecha_referencia = datetime.fromisoformat(args.fecha_referencia) if args.fecha_referencia else None

    for numero in fases:
        if numero == 2:
            metricas = FASES[numero](limite=args.limite)
        elif numero == 4:
            metricas = FASES[numero](fecha_referencia=fecha_referencia)
        else:
            metricas = FASES[numero]()
        if not args.silencioso:
            metricas.imprimir_resumen()


if __name__ == "__main__":
    main()
