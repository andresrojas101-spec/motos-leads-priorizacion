"""Reporte de calidad de datos del pipeline.

Cada corrida deja registro en `ejecuciones` y `metricas_calidad`: cuántos registros se
rechazaron, cuántos valores quedaron inciertos y por qué regla. Es lo que permite
responder "¿qué pasó con los 1.503 leads del archivo?" sin abrir el CSV.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Connection

from src import schema

log = logging.getLogger(__name__)


@dataclass
class Metrica:
    categoria: str
    metrica: str
    valor: float | None
    detalle: str | None = None


@dataclass
class ColectorMetricas:
    """Acumula métricas durante la corrida y las persiste al final."""

    run_id: str
    fase: str
    inicio: datetime = field(default_factory=datetime.now)
    metricas: list[Metrica] = field(default_factory=list)

    def registrar(
        self, categoria: str, metrica: str, valor: float | None, detalle: str | None = None
    ) -> None:
        self.metricas.append(Metrica(categoria, metrica, valor, detalle))

    def persistir(self, conn: Connection, estado: str = "OK") -> None:
        conn.execute(
            schema.ejecuciones.insert(),
            {
                "run_id": self.run_id,
                "fase": self.fase,
                "inicio": self.inicio,
                "fin": datetime.now(),
                "estado": estado,
            },
        )
        if self.metricas:
            conn.execute(
                schema.metricas_calidad.insert(),
                [
                    {
                        "run_id": self.run_id,
                        "categoria": m.categoria,
                        "metrica": m.metrica,
                        "valor": m.valor,
                        "detalle": m.detalle,
                    }
                    for m in self.metricas
                ],
            )

    def imprimir_resumen(self) -> None:
        """Vuelca el reporte de calidad legible por consola."""
        ancho = 78
        print()
        print("=" * ancho)
        print(f" REPORTE DE CALIDAD DE DATOS  ·  run_id={self.run_id}")
        print("=" * ancho)

        categoria_actual = None
        for m in self.metricas:
            if m.categoria != categoria_actual:
                categoria_actual = m.categoria
                print(f"\n[{categoria_actual.upper()}]")
            valor = f"{m.valor:g}" if m.valor is not None else "-"
            detalle = f"   ({m.detalle})" if m.detalle else ""
            print(f"  {m.metrica:.<46} {valor:>8}{detalle}")

        print()
        print("=" * ancho)
