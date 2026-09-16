"""Fase 2 - Extracción con IA desde conversaciones (R13).

Orquesta: leer conversaciones vinculadas -> pedirle al LLM que extraiga los atributos
del enunciado -> validar la respuesta en dos puertas (esquema + semántica) -> reintentar
una vez si la primera puerta falla -> persistir, con `extraccion_status` visible incluso
cuando falla.

Principio heredado de la Fase 1: nada se descarta en silencio y nada se inventa. Una
conversación que falla las dos veces no bloquea el pipeline — el lead correspondiente
sigue siendo priorizable con la información de `leads`, solo que sin enriquecimiento.

**Quién tiene la última palabra**: este módulo solo puebla `enriquecimiento_conversacion`.
Ningún dato de aquí decide la prioridad por sí mismo — eso es responsabilidad exclusiva
del scorecard determinístico de la Fase 3, que puede ponderar según `confianza_global` y
`extraccion_status`.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from sqlalchemy import Connection, select

from src import schema
from src.calidad import ColectorMetricas
from src.config import EXTRACCION_MAX_WORKERS, MODELO_LLM
from src.esquema_extraccion import RespuestaExtraccion, parsear_y_validar, validar_semantica
from src.llm_cliente import ClienteLLM, formatear_transcripcion
from src.normalizadores import EmparejadorModelos

log = logging.getLogger(__name__)

MAX_INTENTOS = 2  # intento inicial + 1 reintento

# Pausa antes del reintento. Con proveedores de capa gratuita (Groq) una parte de los
# fallos son 429 por límite de tasa, no errores de formato: esperar un poco evita que el
# reintento inmediato choque otra vez contra el mismo límite.
ESPERA_ENTRE_INTENTOS_SEGUNDOS = 2.0


def cargar_emparejador_desde_db(conn: Connection) -> EmparejadorModelos:
    """Reconstruye el emparejador de modelos (R6) desde `catalogo_motos`.

    Se lee de la base de datos, no del CSV, para que `--fase 2` sea ejecutable de forma
    independiente sobre un almacén ya poblado, sin depender de que la Fase 1 haya corrido
    en el mismo proceso.
    """
    filas = conn.execute(
        select(schema.catalogo_motos.c.sku, schema.catalogo_motos.c.marca, schema.catalogo_motos.c.linea)
    ).mappings()
    return EmparejadorModelos([dict(f) for f in filas])


def _conversaciones_pendientes(conn: Connection, limite: int | None) -> list[dict]:
    """Conversaciones VINCULADAS que aún no tienen fila en enriquecimiento_conversacion.

    Excluye las huérfanas (R10): no hay lead al cual enriquecer. Filtrar por "pendientes"
    en vez de recargar todo hace la Fase 2 reanudable si se interrumpe a mitad de corrida.
    """
    ya_procesadas = select(schema.enriquecimiento_conversacion.c.conversacion_id)
    consulta = (
        select(schema.conversaciones.c.conversacion_id, schema.conversaciones.c.lead_id)
        .where(schema.conversaciones.c.estado_vinculacion == "VINCULADA")
        .where(schema.conversaciones.c.conversacion_id.notin_(ya_procesadas))
        .order_by(schema.conversaciones.c.conversacion_id)
    )
    if limite:
        consulta = consulta.limit(limite)
    return [dict(fila) for fila in conn.execute(consulta).mappings()]


def _transcripcion_de(conn: Connection, conversacion_id: str) -> str:
    consulta = (
        select(schema.mensajes.c.orden, schema.mensajes.c.emisor, schema.mensajes.c.hora, schema.mensajes.c.texto)
        .where(schema.mensajes.c.conversacion_id == conversacion_id)
        .order_by(schema.mensajes.c.orden)
    )
    mensajes = [dict(f) for f in conn.execute(consulta).mappings()]
    return formatear_transcripcion(mensajes)


def _fila_vacia(conversacion_id: str, lead_id: str, estado: str) -> dict:
    return {
        "conversacion_id": conversacion_id,
        "lead_id": lead_id,
        "modelo_interes_texto": None,
        "sku_interes": None,
        "presupuesto_monto": None,
        "forma_pago": None,
        "intencion": None,
        "objecion_principal": None,
        "pidio_cita": None,
        "pidio_cotizacion": None,
        "confianza_global": None,
        "extraccion_status": estado,
        "modelo_llm": MODELO_LLM,
        "fecha_extraccion": datetime.now(),
    }


def procesar_conversacion(
    conversacion_id: str,
    lead_id: str,
    transcripcion: str,
    cliente: ClienteLLM,
    emparejador: EmparejadorModelos,
) -> tuple[dict, bool]:
    """Ejecuta el ciclo intento -> validar -> reintento para una conversación.

    Devuelve (fila_para_persistir, presupuesto_fue_descartado). No hace I/O de base de
    datos: es la unidad de trabajo que corre en cada hilo del ThreadPoolExecutor.
    """
    datos: RespuestaExtraccion | None = None
    presupuesto_descartado = False
    estado = "FALLO"
    ultimo_error: Exception | None = None

    for intento in range(MAX_INTENTOS):
        es_reintento = intento > 0
        try:
            crudo = cliente.extraer(transcripcion, reintento=es_reintento)
            validado = parsear_y_validar(crudo)
            resultado = validar_semantica(validado)
        except Exception as exc:  # noqa: BLE001 - un intento fallido no debe tumbar el batch
            # Cubre tanto fallas de red/API (anthropic.APIError y similares) como fallas
            # de validacion (ValidationError, JSON mal formado): ambas se tratan igual,
            # como un intento perdido que dispara el reintento en R13.
            ultimo_error = exc
            log.warning(
                "Extraccion fallo en %s (intento %d/%d): %s: %s",
                conversacion_id, intento + 1, MAX_INTENTOS, type(exc).__name__, exc,
            )
            if intento < MAX_INTENTOS - 1:
                time.sleep(ESPERA_ENTRE_INTENTOS_SEGUNDOS)
            continue

        datos = resultado.datos
        presupuesto_descartado = resultado.presupuesto_descartado
        estado = "REINTENTO_OK" if es_reintento else "OK"
        break

    if datos is None:
        log.error(
            "Conversacion %s sin extraccion valida tras %d intentos: %s",
            conversacion_id, MAX_INTENTOS, ultimo_error,
        )
        return _fila_vacia(conversacion_id, lead_id, "FALLO"), False

    modelo = emparejador.emparejar(datos.modelo_interes_texto)

    fila = {
        "conversacion_id": conversacion_id,
        "lead_id": lead_id,
        "modelo_interes_texto": datos.modelo_interes_texto,
        "sku_interes": modelo.sku,
        "presupuesto_monto": datos.presupuesto_monto,
        "forma_pago": datos.forma_pago.value,
        "intencion": datos.intencion.value,
        "objecion_principal": datos.objecion_principal.value,
        "pidio_cita": datos.pidio_cita,
        "pidio_cotizacion": datos.pidio_cotizacion,
        "confianza_global": datos.confianza_global,
        "extraccion_status": estado,
        "modelo_llm": MODELO_LLM,
        "fecha_extraccion": datetime.now(),
    }
    return fila, presupuesto_descartado


TAMANO_LOTE_COMMIT = 25  # ~5 min de trabajo a 5 llamadas/min: poco que perder si algo falla


def ejecutar_extraccion(
    conn: Connection,
    cliente: ClienteLLM,
    metricas: ColectorMetricas,
    *,
    limite: int | None = None,
    max_workers: int = EXTRACCION_MAX_WORKERS,
    tamano_lote_commit: int = TAMANO_LOTE_COMMIT,
) -> None:
    """Punto de entrada de la Fase 2.

    `limite` permite correr sobre una muestra pequeña (útil para validar costo y calidad
    antes de lanzar las ~665 conversaciones vinculadas). Las llamadas al LLM son
    I/O-bound, por lo que se paralelizan con hilos.

    **Persistencia incremental, no al final**: para un batch de ~665 conversaciones que
    puede tardar horas en la capa gratuita, acumular todo en memoria y escribir un único
    INSERT al final significa perder TODO el progreso si el proceso muere por cualquier
    motivo (corte de luz, la terminal se cierra, una excepción no capturada) un segundo
    antes de terminar. Cada `tamano_lote_commit` conversaciones completadas se escriben y
    se hace `conn.commit()` de inmediato — el llamador (`pipeline.py`) debe usar
    `engine.connect()`, no `engine.begin()`, para que estos commits intermedios sean
    reales y no queden atrapados dentro de una única transacción de horas.
    """
    emparejador = cargar_emparejador_desde_db(conn)
    pendientes = _conversaciones_pendientes(conn, limite)

    if not pendientes:
        log.info("No hay conversaciones pendientes de extraccion")
        metricas.registrar("extraccion", "pendientes", 0)
        return

    transcripciones = {p["conversacion_id"]: _transcripcion_de(conn, p["conversacion_id"]) for p in pendientes}

    filas: list[dict] = []  # se conserva todo para las metricas finales del resumen
    lote: list[dict] = []
    presupuestos_descartados = 0

    def volcar_lote() -> None:
        if lote:
            conn.execute(schema.enriquecimiento_conversacion.insert(), lote)
            conn.commit()
            lote.clear()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futuros = {
            pool.submit(
                procesar_conversacion,
                p["conversacion_id"],
                p["lead_id"],
                transcripciones[p["conversacion_id"]],
                cliente,
                emparejador,
            ): p["conversacion_id"]
            for p in pendientes
        }
        for i, futuro in enumerate(as_completed(futuros), start=1):
            fila, descartado = futuro.result()
            filas.append(fila)
            lote.append(fila)
            presupuestos_descartados += int(descartado)
            if len(lote) >= tamano_lote_commit:
                volcar_lote()
                log.info(
                    "Extraccion: %d/%d conversaciones procesadas (checkpoint guardado)",
                    i, len(futuros),
                )
            elif i % 50 == 0 or i == len(futuros):
                log.info("Extraccion: %d/%d conversaciones procesadas", i, len(futuros))

    volcar_lote()  # remanente que no alcanzo a completar un lote

    conteo_estado = {"OK": 0, "REINTENTO_OK": 0, "FALLO": 0}
    confianzas = []
    conteo_intencion: dict[str, int] = {}
    conteo_objecion: dict[str, int] = {}
    for f in filas:
        conteo_estado[f["extraccion_status"]] += 1
        if f["confianza_global"] is not None:
            confianzas.append(f["confianza_global"])
        if f["intencion"]:
            conteo_intencion[f["intencion"]] = conteo_intencion.get(f["intencion"], 0) + 1
        if f["objecion_principal"]:
            conteo_objecion[f["objecion_principal"]] = conteo_objecion.get(f["objecion_principal"], 0) + 1

    metricas.registrar("extraccion", "conversaciones_procesadas", len(filas))
    metricas.registrar("extraccion", "ok_primer_intento", conteo_estado["OK"])
    metricas.registrar("extraccion", "ok_tras_reintento", conteo_estado["REINTENTO_OK"])
    metricas.registrar("extraccion", "fallo_definitivo", conteo_estado["FALLO"], "R13")
    metricas.registrar(
        "extraccion",
        "presupuesto_fuera_de_rango_descartado",
        presupuestos_descartados,
        f"rango plausible en esquema_extraccion.py",
    )
    if confianzas:
        metricas.registrar("extraccion", "confianza_promedio", round(sum(confianzas) / len(confianzas), 3))
        metricas.registrar("extraccion", "confianza_minima", round(min(confianzas), 3))
    for intencion, n in sorted(conteo_intencion.items()):
        metricas.registrar("extraccion_intencion", intencion, n)
    for objecion, n in sorted(conteo_objecion.items()):
        metricas.registrar("extraccion_objecion", objecion, n)

    log.info(
        "Extraccion completada: %d OK, %d OK-tras-reintento, %d fallo definitivo (de %d)",
        conteo_estado["OK"], conteo_estado["REINTENTO_OK"], conteo_estado["FALLO"], len(filas),
    )
