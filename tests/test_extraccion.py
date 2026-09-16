"""Tests de la orquestación de la Fase 2 (R13): reintento, falla controlada y batch.

Usa un ClienteLLM falso (`ClienteLLMFalso`) que implementa el mismo Protocol que
`ClienteAnthropic` sin tocar la red. Esto es lo que hace testeable la lógica de negocio
(prompt -> validar -> reintentar) sin necesitar una ANTHROPIC_API_KEY real.
"""

from __future__ import annotations

import threading
from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from src import schema
from src.calidad import ColectorMetricas
from src.esquema_extraccion import ValidationError
from src.extraccion import ejecutar_extraccion, procesar_conversacion
from src.normalizadores import EmparejadorModelos

RESPUESTA_VALIDA = {
    "modelo_interes_texto": "Bajaj Discover 125",
    "presupuesto_monto": 1_000_000,
    "forma_pago": "CREDITO",
    "intencion": "ALTA",
    "objecion_principal": "NINGUNA",
    "pidio_cita": True,
    "pidio_cotizacion": False,
    "confianza_global": 0.9,
}

RESPUESTA_CON_ENUM_INVALIDO = dict(RESPUESTA_VALIDA, forma_pago="EFECTIVO_MAGICO")


@pytest.fixture(autouse=True)
def _sin_espera_entre_reintentos(monkeypatch):
    """Evita que los tests de reintento paguen el backoff real (pensado para 429 de red)."""
    monkeypatch.setattr("src.extraccion.ESPERA_ENTRE_INTENTOS_SEGUNDOS", 0.0)


class ClienteLLMFalso:
    """Doble de pruebas: no hace red, devuelve lo que decida `respuesta_fn`.

    `respuesta_fn(transcripcion, reintento) -> dict | Exception` permite que cada test
    controle exactamente qué pasa en el primer intento y en el reintento, incluso bajo
    ejecución concurrente (protegido con lock para el conteo de llamadas).
    """

    def __init__(self, respuesta_fn):
        self._respuesta_fn = respuesta_fn
        self.llamadas = 0
        self._lock = threading.Lock()

    def extraer(self, transcripcion: str, *, reintento: bool = False) -> dict:
        with self._lock:
            self.llamadas += 1
        resultado = self._respuesta_fn(transcripcion, reintento)
        if isinstance(resultado, Exception):
            raise resultado
        return resultado


@pytest.fixture
def emparejador():
    catalogo = [
        {"sku": "SKU-012", "marca": "Bajaj", "linea": "Discover 125"},
        {"sku": "SKU-013", "marca": "Suzuki", "linea": "GN 125"},
    ]
    return EmparejadorModelos(catalogo)


# --------------------------------------------------------------------------------------
# procesar_conversacion - unidad de trabajo (sin DB)
# --------------------------------------------------------------------------------------


def test_extraccion_exitosa_en_primer_intento(emparejador):
    cliente = ClienteLLMFalso(lambda t, r: RESPUESTA_VALIDA)
    fila, descartado = procesar_conversacion("CONV-1", "LD-1", "hola", cliente, emparejador)

    assert fila["extraccion_status"] == "OK"
    assert fila["sku_interes"] == "SKU-012"
    assert fila["intencion"] == "ALTA"
    assert fila["confianza_global"] == 0.9
    assert descartado is False
    assert cliente.llamadas == 1


def test_reintenta_cuando_el_primer_intento_no_cumple_el_esquema(emparejador):
    """El primer intento trae un enum inventado; el reintento sí es válido."""
    respuestas = iter([RESPUESTA_CON_ENUM_INVALIDO, RESPUESTA_VALIDA])
    cliente = ClienteLLMFalso(lambda t, r: next(respuestas))

    fila, _ = procesar_conversacion("CONV-1", "LD-1", "hola", cliente, emparejador)

    assert fila["extraccion_status"] == "REINTENTO_OK"
    assert cliente.llamadas == 2


def test_reintento_se_pide_explicitamente_como_reintento(emparejador):
    """El segundo llamado debe indicarle al cliente reintento=True (cambia el prompt)."""
    banderas = []

    def respuesta_fn(t, reintento):
        banderas.append(reintento)
        return RESPUESTA_CON_ENUM_INVALIDO if not reintento else RESPUESTA_VALIDA

    cliente = ClienteLLMFalso(respuesta_fn)
    procesar_conversacion("CONV-1", "LD-1", "hola", cliente, emparejador)

    assert banderas == [False, True]


def test_fallo_definitivo_tras_agotar_los_intentos(emparejador):
    """Dos intentos invalidos seguidos: la conversacion queda marcada FALLO, no bloquea."""
    cliente = ClienteLLMFalso(lambda t, r: RESPUESTA_CON_ENUM_INVALIDO)

    fila, descartado = procesar_conversacion("CONV-1", "LD-1", "hola", cliente, emparejador)

    assert fila["extraccion_status"] == "FALLO"
    assert fila["sku_interes"] is None
    assert fila["intencion"] is None
    assert fila["confianza_global"] is None
    assert descartado is False
    assert cliente.llamadas == 2


def test_error_de_red_cuenta_como_intento_fallido_y_permite_reintentar(emparejador):
    """Una excepcion de conexion en el primer intento no debe tumbar el proceso."""
    respuestas = iter([ConnectionError("timeout"), RESPUESTA_VALIDA])
    cliente = ClienteLLMFalso(lambda t, r: next(respuestas))

    fila, _ = procesar_conversacion("CONV-1", "LD-1", "hola", cliente, emparejador)

    assert fila["extraccion_status"] == "REINTENTO_OK"


def test_presupuesto_implausible_se_descarta_sin_marcar_fallo(emparejador):
    """500 millones no es una cuota inicial real: se anula el campo, no la extraccion."""
    respuesta = dict(RESPUESTA_VALIDA, presupuesto_monto=500_000_000)
    cliente = ClienteLLMFalso(lambda t, r: respuesta)

    fila, descartado = procesar_conversacion("CONV-1", "LD-1", "hola", cliente, emparejador)

    assert fila["extraccion_status"] == "OK"
    assert fila["presupuesto_monto"] is None
    assert descartado is True


def test_modelo_no_mencionado_no_bloquea_la_extraccion(emparejador):
    """Conversacion de 'solo estaba mirando': sin modelo, sin presupuesto, sigue OK."""
    respuesta = {
        "modelo_interes_texto": None,
        "presupuesto_monto": None,
        "forma_pago": "NO_INFORMA",
        "intencion": "BAJA",
        "objecion_principal": "SOLO_COMPARANDO",
        "pidio_cita": False,
        "pidio_cotizacion": False,
        "confianza_global": 0.7,
    }
    cliente = ClienteLLMFalso(lambda t, r: respuesta)

    fila, _ = procesar_conversacion("CONV-1", "LD-1", "hola", cliente, emparejador)

    assert fila["extraccion_status"] == "OK"
    assert fila["sku_interes"] is None
    assert fila["intencion"] == "BAJA"


# --------------------------------------------------------------------------------------
# ejecutar_extraccion - batch completo sobre una base de datos en memoria
# --------------------------------------------------------------------------------------


@pytest.fixture
def conn():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    schema.metadata.create_all(engine)
    with engine.connect() as c:
        yield c


def _poblar_conversacion_minima(conn):
    conn.execute(
        schema.catalogo_motos.insert(),
        [
            {
                "sku": "SKU-012", "marca": "Bajaj", "linea": "Discover 125",
                "modelo_normalizado": "Bajaj Discover 125", "cilindraje": 124,
                "segmento": "Trabajo", "precio_lista": 6_990_000, "unidades_disponibles": 6,
            }
        ],
    )
    conn.execute(
        schema.conversaciones.insert(),
        [
            {
                "conversacion_id": "CONV-1", "lead_id": "LD-1", "lead_id_declarado": "LD-1",
                "empresa_id": "EMP-01", "canal": "WHATSAPP", "fecha_inicio": datetime(2026, 8, 1),
                "num_mensajes": 1, "estado_vinculacion": "VINCULADA",
            },
            {
                # Huerfana (R10): no debe llegar nunca a procesar_conversacion.
                "conversacion_id": "CONV-2", "lead_id": None, "lead_id_declarado": "LD-99999",
                "empresa_id": None, "canal": "WHATSAPP", "fecha_inicio": datetime(2026, 8, 1),
                "num_mensajes": 1, "estado_vinculacion": "HUERFANA",
            },
        ],
    )
    conn.execute(
        schema.mensajes.insert(),
        [
            {"conversacion_id": "CONV-1", "orden": 1, "emisor": "cliente", "hora": "10:00",
             "texto": "Me interesa la Bajaj Discover 125"},
            {"conversacion_id": "CONV-2", "orden": 1, "emisor": "cliente", "hora": "09:00", "texto": "hola"},
        ],
    )
    conn.commit()


def test_ejecutar_extraccion_excluye_huerfanas_y_persiste_ok(conn):
    _poblar_conversacion_minima(conn)
    cliente = ClienteLLMFalso(lambda t, r: RESPUESTA_VALIDA)
    metricas = ColectorMetricas(run_id="test-fase2", fase="fase2")

    ejecutar_extraccion(conn, cliente, metricas, max_workers=1)
    conn.commit()

    filas = conn.execute(select(schema.enriquecimiento_conversacion)).mappings().all()
    assert len(filas) == 1
    assert filas[0]["conversacion_id"] == "CONV-1"
    assert filas[0]["extraccion_status"] == "OK"
    assert filas[0]["sku_interes"] == "SKU-012"
    assert cliente.llamadas == 1


def test_ejecutar_extraccion_es_reanudable(conn):
    """Una segunda corrida no vuelve a llamar al LLM sobre conversaciones ya procesadas."""
    _poblar_conversacion_minima(conn)
    cliente = ClienteLLMFalso(lambda t, r: RESPUESTA_VALIDA)
    metricas = ColectorMetricas(run_id="test-fase2", fase="fase2")

    ejecutar_extraccion(conn, cliente, metricas, max_workers=1)
    conn.commit()
    assert cliente.llamadas == 1

    ejecutar_extraccion(conn, cliente, metricas, max_workers=1)
    conn.commit()
    assert cliente.llamadas == 1  # sigue en 1: no habia pendientes


def test_ejecutar_extraccion_respeta_el_limite(conn):
    conn.execute(
        schema.catalogo_motos.insert(),
        [{"sku": "SKU-013", "marca": "Suzuki", "linea": "GN 125", "modelo_normalizado": "Suzuki GN 125",
          "cilindraje": 124, "segmento": "Trabajo", "precio_lista": 7_490_000, "unidades_disponibles": 20}],
    )
    conn.execute(
        schema.conversaciones.insert(),
        [
            {"conversacion_id": f"CONV-{i}", "lead_id": f"LD-{i}", "lead_id_declarado": f"LD-{i}",
             "empresa_id": "EMP-01", "canal": "WHATSAPP", "fecha_inicio": datetime(2026, 8, 1),
             "num_mensajes": 1, "estado_vinculacion": "VINCULADA"}
            for i in range(1, 6)
        ],
    )
    conn.execute(
        schema.mensajes.insert(),
        [{"conversacion_id": f"CONV-{i}", "orden": 1, "emisor": "cliente", "hora": "10:00", "texto": "hola"}
         for i in range(1, 6)],
    )
    conn.commit()

    cliente = ClienteLLMFalso(lambda t, r: RESPUESTA_VALIDA)
    metricas = ColectorMetricas(run_id="test-limite", fase="fase2")

    ejecutar_extraccion(conn, cliente, metricas, limite=2, max_workers=1)
    conn.commit()

    filas = conn.execute(select(schema.enriquecimiento_conversacion)).mappings().all()
    assert len(filas) == 2
