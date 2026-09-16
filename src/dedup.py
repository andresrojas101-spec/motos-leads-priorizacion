"""Fase 1 - Resolución de identidad de cliente y deduplicación multicanal (R8)."""

from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy import Connection

from src import schema
from src.calidad import ColectorMetricas
from src.normalizadores import completitud_nombre

log = logging.getLogger(__name__)


def _elegir_lead_canonico(grupo: list[dict], con_conversacion: set[str]) -> dict:
    """Elige el lead que representa a la persona.

    Prioridad: (1) tener conversación asociada — es el que trae contexto real para el
    asesor; (2) el registro más reciente. Ambos criterios son deterministas, de modo que
    dos corridas sobre los mismos datos producen la misma elección.
    """
    return max(
        grupo,
        key=lambda lead: (
            lead["lead_id"] in con_conversacion,
            lead["fecha_registro"],
        ),
    )


def _nombre_canonico(grupo: list[dict]) -> str | None:
    """El nombre más completo del grupo.

    El mismo cliente llega abreviado por un canal y completo por otro
    ('J. Pérez Arias' vs 'JULIÁN PÉREZ ARIAS'): se conserva el que más aporta.
    """
    nombres = [lead["nombre_normalizado"] for lead in grupo if lead["nombre_normalizado"]]
    if not nombres:
        return None
    return max(nombres, key=lambda n: (completitud_nombre(n), len(n)))


def construir_personas(
    candidatos: list[dict], con_conversacion: set[str], metricas: ColectorMetricas
) -> list[dict]:
    """R8 - Agrupa leads en personas por (empresa_id, telefono) y marca los canónicos.

    Muta `candidatos` agregando `persona_id` y `es_lead_canonico`. Devuelve las personas.

    La llave incluye `empresa_id` deliberadamente: el 65% de los teléfonos duplicados
    cruzan empresas distintas y fusionarlos rompería la separación por empresa que exige
    el requisito obligatorio #8 del enunciado.
    """
    grupos: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for lead in candidatos:
        grupos[(lead["empresa_id"], lead["telefono_normalizado"])].append(lead)

    personas: list[dict] = []
    leads_secundarios = 0

    for persona_id, ((empresa_id, telefono), grupo) in enumerate(sorted(grupos.items()), start=1):
        canonico = _elegir_lead_canonico(grupo, con_conversacion)

        for lead in grupo:
            lead["persona_id"] = persona_id
            lead["es_lead_canonico"] = lead["lead_id"] == canonico["lead_id"]
            if not lead["es_lead_canonico"]:
                leads_secundarios += 1

        emails = [lead["email_normalizado"] for lead in grupo if lead["email_normalizado"]]
        ciudades = [lead["ciudad_normalizada"] for lead in grupo if lead["ciudad_normalizada"]]
        fechas = [lead["fecha_registro"] for lead in grupo]

        personas.append(
            {
                "persona_id": persona_id,
                "empresa_id": empresa_id,
                "telefono_normalizado": telefono,
                "nombre_canonico": _nombre_canonico(grupo),
                "email_canonico": emails[0] if emails else None,
                "ciudad_normalizada": canonico["ciudad_normalizada"] or (ciudades[0] if ciudades else None),
                "total_leads": len(grupo),
                "primer_registro": min(fechas),
                "ultimo_registro": max(fechas),
            }
        )

    grupos_multiples = sum(1 for p in personas if p["total_leads"] > 1)

    # Cuántos habrían colapsado si la llave fuera solo el teléfono: mide el costo de
    # respetar la separación por empresa, para poder sustentarlo ante el negocio.
    telefonos_globales = {lead["telefono_normalizado"] for lead in candidatos}
    colapsos_si_global = len(candidatos) - len(telefonos_globales)

    metricas.registrar("personas", "total", len(personas), "R8 - llave (empresa_id, telefono)")
    metricas.registrar("personas", "con_multiples_leads", grupos_multiples)
    metricas.registrar("personas", "leads_secundarios", leads_secundarios)
    metricas.registrar("personas", "colapsos_reales", len(candidatos) - len(personas))
    metricas.registrar(
        "personas",
        "colapsos_si_llave_global",
        colapsos_si_global,
        "descartado: fusionaria clientes de empresas distintas",
    )

    log.info(
        "Personas resueltas: %d (%d con mas de un lead, %d leads secundarios)",
        len(personas), grupos_multiples, leads_secundarios,
    )
    return personas


def persistir_personas(conn: Connection, personas: list[dict]) -> None:
    conn.execute(schema.personas.insert(), personas)
