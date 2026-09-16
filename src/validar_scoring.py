"""Valida la fórmula de scoring (src/scoring.py) contra historico_cierres.csv.

No es parte del pipeline de producción — es la herramienta que respalda la decisión de
diseño documentada en docs/scoring.md sección 7. Se corre a mano cuando se cambia la
fórmula, para confirmar (con datos, no con intuición) que sigue separando a los leads que
cierran de los que no.

Uso: python -m src.validar_scoring
"""

from __future__ import annotations

import pandas as pd

from src.config import ARCHIVO_HISTORICO
from src.scoring import EntradaScore, calcular_score

_MAPA_FORMA_PAGO = {"contado": "CONTADO", "credito": "CREDITO", "no_informa": "NO_INFORMA"}


def _entrada_desde_fila_historico(fila) -> EntradaScore:
    """Traduce una fila de historico_cierres.csv al contrato de EntradaScore.

    `horas_al_primer_contacto` del histórico se usa directamente como `horas_sin_avance`:
    es la aplicación retrospectiva de "si hubiéramos scoreado este lead y actuado cuando
    el score lo pedía, hasta cuántas horas habría esperado hasta el contacto real".
    """
    return EntradaScore(
        horas_sin_avance=fila.horas_al_primer_contacto,
        forma_pago=_MAPA_FORMA_PAGO[fila.forma_pago_declarada],
        cuota_inicial=fila.manifesto_cuota_inicial,
        pidio_cita=(fila.pidio_cita == "SI"),
    )


def validar() -> pd.DataFrame:
    df = pd.read_csv(ARCHIVO_HISTORICO)
    gestionados = df[df.desenlace != "Sin gestión"].copy()

    resultados = [calcular_score(_entrada_desde_fila_historico(f)) for f in gestionados.itertuples()]
    gestionados["score_total"] = [r.score_total for r in resultados]
    gestionados["temperatura"] = [r.temperatura for r in resultados]
    gestionados["cerrado"] = gestionados.desenlace == "Cerrado"

    return gestionados


def imprimir_reporte(gestionados: pd.DataFrame) -> None:
    print("=" * 78)
    print(" VALIDACION RETRO-ACTIVA DEL SCORING CONTRA historico_cierres.csv")
    print("=" * 78)
    print(f"\nTotal gestionados: {len(gestionados)}  |  Tasa de cierre base: "
          f"{100 * gestionados.cerrado.mean():.2f}%\n")

    print("--- Por temperatura ---")
    resumen = gestionados.groupby("temperatura").agg(
        n=("cerrado", "size"), tasa_cierre=("cerrado", "mean"), score_prom=("score_total", "mean")
    ).reindex(["CALIENTE", "TIBIO", "FRIO"])
    for temp, fila in resumen.iterrows():
        print(f"  {temp:10} n={int(fila.n):5}  score_prom={fila.score_prom:6.2f}  "
              f"tasa_cierre={100*fila.tasa_cierre:5.2f}%")

    lift = resumen.loc["CALIENTE", "tasa_cierre"] / resumen.loc["FRIO", "tasa_cierre"]
    print(f"\n  Lift CALIENTE vs FRIO: {lift:.2f}x")

    print("\n--- Por decil de score (granularidad fina) ---")
    gestionados["decil"] = pd.qcut(gestionados.score_total, 10, duplicates="drop")
    por_decil = gestionados.groupby("decil", observed=True).agg(
        n=("cerrado", "size"), tasa_cierre=("cerrado", "mean")
    ).sort_index()
    for rango, fila in por_decil.iterrows():
        print(f"  {str(rango):20} n={int(fila.n):5}  tasa_cierre={100*fila.tasa_cierre:5.2f}%")

    print("\n" + "=" * 78)


if __name__ == "__main__":
    imprimir_reporte(validar())
