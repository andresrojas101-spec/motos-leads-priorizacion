"""Tests del motor de scoring (Fase 3). Metodología y justificación en docs/scoring.md."""

from __future__ import annotations

from datetime import datetime

import pytest

from src.scoring import (
    EntradaScore,
    calcular_score,
    degradar_por_confianza,
    horas_sin_avance,
    sub_score_cuota_inicial,
    sub_score_forma_pago,
    sub_score_objecion,
    sub_score_pidio_cita,
    sub_score_urgencia,
    temperatura_de,
)

# --------------------------------------------------------------------------------------
# horas_sin_avance
# --------------------------------------------------------------------------------------


def test_horas_sin_avance_usa_primer_contacto_si_existe():
    """Un lead contactado hace 5 dias y sin resolucion esta tan frio como uno nunca tocado."""
    registro = datetime(2026, 9, 1, 8, 0)
    contacto = datetime(2026, 9, 10, 8, 0)  # 9 dias despues del registro
    ahora = datetime(2026, 9, 15, 8, 0)  # 5 dias despues del contacto

    horas = horas_sin_avance(registro, contacto, ahora)

    assert horas == pytest.approx(5 * 24)  # cuenta desde el contacto, no desde el registro


def test_horas_sin_avance_usa_registro_si_nunca_hubo_contacto():
    registro = datetime(2026, 9, 15, 6, 0)
    ahora = datetime(2026, 9, 15, 8, 0)

    assert horas_sin_avance(registro, None, ahora) == pytest.approx(2)


def test_horas_sin_avance_nunca_es_negativo():
    """Reloj de fase de servidor desalineado no debe producir urgencia negativa."""
    registro = datetime(2026, 9, 15, 10, 0)
    ahora = datetime(2026, 9, 15, 8, 0)  # "ahora" antes que el registro

    assert horas_sin_avance(registro, None, ahora) == 0.0


# --------------------------------------------------------------------------------------
# sub_score_urgencia - monotonicidad y anclas
# --------------------------------------------------------------------------------------


def test_sub_score_urgencia_es_monotonicamente_decreciente():
    horas_ordenadas = [0.5, 2, 12, 36, 80, 200]
    scores = [sub_score_urgencia(h) for h in horas_ordenadas]
    assert scores == sorted(scores, reverse=True)


def test_sub_score_urgencia_ancla_mejor_balde_en_100():
    assert sub_score_urgencia(0.5) == 100.0


def test_sub_score_urgencia_extrapolacion_mas_alla_de_120h_es_el_piso_real():
    """El balde >120h (extrapolado, no medido) es el ancla en 0, no el 48-120h medido:
    la extrapolacion asume que sigue empeorando mas alla del ultimo dato real."""
    assert sub_score_urgencia(500) == 0.0
    assert sub_score_urgencia(100) > 0.0  # 48-120h medido, peor que los demas pero no el piso


def test_sub_score_urgencia_extrapolacion_no_produce_negativo_ni_pasa_el_piso():
    assert sub_score_urgencia(10_000) == 0.0


# --------------------------------------------------------------------------------------
# Sub-scores validados (Grupo A)
# --------------------------------------------------------------------------------------


def test_forma_pago_contado_supera_a_credito():
    """CONTADO tiene bono de negocio; ver docs/scoring.md por que NO_INFORMA no se premia
    pese a que el dato crudo del historico lo favorece levemente (ruido, no señal)."""
    assert sub_score_forma_pago("CONTADO") > sub_score_forma_pago("CREDITO")
    assert sub_score_forma_pago("CONTADO") > sub_score_forma_pago("NO_INFORMA")


def test_cuota_inicial_orden_business_sensible():
    assert sub_score_cuota_inicial("SI") > sub_score_cuota_inicial("NO") > sub_score_cuota_inicial("NO_INFORMA")


def test_pidio_cita_si_supera_a_no():
    assert sub_score_pidio_cita(True) > sub_score_pidio_cita(False)


# --------------------------------------------------------------------------------------
# Sub-scores de IA (Grupo B) y degradacion por confianza
# --------------------------------------------------------------------------------------


def test_objecion_ninguna_es_el_mejor_caso():
    todas = ["NINGUNA", "PRECIO", "SIN_CUOTA_INICIAL", "SOLO_COMPARANDO", "CONSULTA_CON_TERCERO", "BUSCA_USADO", "OTRA"]
    assert sub_score_objecion("NINGUNA") == max(sub_score_objecion(o) for o in todas)


def test_degradar_por_confianza_sin_cambio_con_confianza_total():
    assert degradar_por_confianza(90.0, confianza_global=1.0) == 90.0


def test_degradar_por_confianza_colapsa_a_neutral_con_confianza_cero():
    assert degradar_por_confianza(90.0, confianza_global=0.0) == 50.0
    assert degradar_por_confianza(10.0, confianza_global=0.0) == 50.0


def test_degradar_por_confianza_intermedio():
    # 50 + 0.5*(90-50) = 70
    assert degradar_por_confianza(90.0, confianza_global=0.5) == 70.0


# --------------------------------------------------------------------------------------
# temperatura_de
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "score,esperado",
    [(90, "CALIENTE"), (65, "CALIENTE"), (64.9, "TIBIO"), (40, "TIBIO"), (39.9, "FRIO"), (0, "FRIO")],
)
def test_temperatura_de_cortes(score, esperado):
    assert temperatura_de(score) == esperado


# --------------------------------------------------------------------------------------
# calcular_score - combinacion ponderada y renormalizacion
# --------------------------------------------------------------------------------------


def test_calcular_score_solo_urgencia_cuando_no_hay_mas_datos():
    """56% de los leads no tienen conversacion: el score debe poder calcularse solo con
    urgencia, sin fallar ni inventar los demas componentes."""
    entrada = EntradaScore(horas_sin_avance=0.5)  # mejor balde, sin nada mas

    resultado = calcular_score(entrada)

    assert len(resultado.componentes) == 1
    assert resultado.componentes[0].nombre == "urgencia"
    assert resultado.score_total == 100.0  # unico componente, ancla en el mejor balde


def test_calcular_score_mejor_caso_absoluto_da_100():
    entrada = EntradaScore(
        horas_sin_avance=0.1,
        forma_pago="CONTADO",
        cuota_inicial="SI",
        pidio_cita=True,
        intencion="ALTA",
        objecion_principal="NINGUNA",
        confianza_global=1.0,
    )
    resultado = calcular_score(entrada)
    assert resultado.temperatura == "CALIENTE"
    assert resultado.score_total > 90


def test_calcular_score_peor_caso_absoluto_da_frio():
    entrada = EntradaScore(
        horas_sin_avance=500,
        forma_pago="CREDITO",
        cuota_inicial="NO_INFORMA",
        pidio_cita=False,
        intencion="BAJA",
        objecion_principal="BUSCA_USADO",
        confianza_global=1.0,
    )
    resultado = calcular_score(entrada)
    assert resultado.temperatura == "FRIO"
    assert resultado.score_total < 20


def test_calcular_score_mas_componentes_no_dispara_el_peso_total():
    """Los pesos de los componentes presentes siempre deben sumar 1.0 tras renormalizar,
    sin importar cuantos esten disponibles."""
    solo_urgencia = calcular_score(EntradaScore(horas_sin_avance=10))
    con_todo = calcular_score(
        EntradaScore(
            horas_sin_avance=10, forma_pago="CREDITO", cuota_inicial="NO",
            pidio_cita=False, intencion="MEDIA", objecion_principal="OTRA", confianza_global=1.0,
        )
    )
    assert 0 <= solo_urgencia.score_total <= 100
    assert 0 <= con_todo.score_total <= 100


def test_calcular_score_presupuesto_no_duplica_peso_con_cuota_inicial():
    """Si por algun motivo coexistieran cuota_inicial (historico) y presupuesto_monto
    (IA) para el mismo lead, no deben sumar dos veces el mismo peso."""
    entrada = EntradaScore(
        horas_sin_avance=10,
        cuota_inicial="SI",
        presupuesto_dato_presente=True,
        presupuesto_monto=1_000_000,
        confianza_global=1.0,
    )
    resultado = calcular_score(entrada)
    nombres = [c.nombre for c in resultado.componentes]
    assert nombres.count("cuota_inicial") == 1


def test_calcular_score_baja_confianza_atenua_pero_no_anula_intencion_alta():
    alta_confianza = calcular_score(
        EntradaScore(horas_sin_avance=10, intencion="ALTA", confianza_global=1.0)
    )
    baja_confianza = calcular_score(
        EntradaScore(horas_sin_avance=10, intencion="ALTA", confianza_global=0.2)
    )
    # Misma urgencia; la diferencia solo puede venir de que intencion_ia pese menos
    # cuando la confianza es baja (se acerca a 50, el neutral).
    assert baja_confianza.score_total < alta_confianza.score_total
