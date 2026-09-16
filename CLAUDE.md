# CLAUDE.md

Contexto operativo del proyecto para sesiones de Claude Code.

## Qué es esto

Assessment técnico para el cargo **Analista de IA** en Motos y Motores del Norte S.A.S.
(comercializadora multimarca de motos: Honda, Bajaj, Suzuki, AKT, Hero).

**Problema de negocio**: >3.000 leads/mes llegan por tres canales a una sola bandeja y se
atienden por orden de llegada (FIFO). Cierra menos de 1 de cada 10. La información que
define la prioridad (modelo, cuota inicial, forma de pago, si pidió cita) está enterrada
en texto libre de conversaciones de WhatsApp y nunca llega al CRM.

**Solución a construir**: pipeline automatizado que ingiere los archivos crudos, normaliza,
deduplica, extrae contexto con IA desde las conversaciones, y produce una lista priorizada
de gestión diaria por asesor, persistida en base de datos y publicada en una URL.

El enunciado completo está en `../Assessment-Analista-IA-Enunciado.pdf`.

## Decisiones de arquitectura ya tomadas

| Decisión | Elección | Razón |
|---|---|---|
| Enfoque general | Pipeline batch determinístico + **una** llamada LLM de extracción estructurada | 7 de los 8 requisitos del alcance mínimo son ingeniería de datos; solo la lectura de conversaciones necesita IA |
| Multi-agente | **Descartado** | No hay planeación dinámica ni selección de herramientas; el score debe ser explicable y auditable, no producto del razonamiento de un LLM |
| Scoring | Scorecard de reglas ponderadas, calibrado contra `historico_cierres.csv` | El enunciado exige "lógica explicable y sustentada"; el histórico tiene señal débil salvo velocidad de contacto |
| Base de datos | SQLAlchemy Core + `DATABASE_URL` (SQLite local → Postgres/Supabase en prod) | Mismo código en dev y prod; desacopla el runner de ingesta del tablero |
| Orquestación | GitHub Actions (cron + `workflow_dispatch`) | Un solo disparo corre todo; cumple "sin intervención manual" |
| Publicación | Streamlit Cloud | Capa gratuita, Python de punta a punta, rápido de desplegar |

**La última palabra sobre prioridad siempre es del motor de reglas, nunca del LLM.**
El LLM solo extrae atributos; el score los pondera de forma transparente.

## Particularidades de los datos (ya verificadas, no re-investigar)

Los datos son sintéticos con inconsistencias deliberadas. Hallazgos confirmados:

- **`leads.csv` (1.503 filas)**: es la tabla sucia. `historico_cierres.csv` y `catalogo_motos.csv`
  están limpias y son consistentes entre sí (precios y mapeo PV↔empresa coinciden al 100%).
- **Fechas**: 4 formatos mezclados. ~480 fechas con `/` son **ambiguas e irresolubles**
  (DD/MM vs MM/DD, ambos componentes ≤12). Se asume DD/MM (estándar CO) y se marca la confianza.
- **Ciudades**: hasta 6 variantes por ciudad (`Bogotá D.C.`, `BOGOTA`, `Bogota DC`, ...).
  También `Rio Negro` vs `Rionegro`, `B/quilla` vs `Barranquilla`.
- **Casing**: `canal` tiene 9 variantes para 3 valores; `estado_gestion` 10 para 6.
- **Fila basura**: `LD-01501` = `"prueba prueba"`, teléfono `300123`, fecha `2026-08-33` (día 33).
- **Duplicados exactos**: `LD-00011` y `LD-00251` aparecen dos veces, idénticos.
- **Duplicados de persona**: 141 teléfonos repetidos en 283 filas. **91 de esos 141 grupos
  cruzan `empresa_id` distinto** → ver decisión de tenancy abajo.
- **Conversaciones**: las 677 están etiquetadas `canal="WhatsApp"`, pero solo el 55% de sus
  leads dice WhatsApp en `leads.csv` → se interpreta como canal de **origen** ≠ canal de **atención**.
- **12 `lead_id` huérfanos** en `conversaciones.json` (rango `LD-9xxxx`, no existen en `leads.csv`).
- **86 leads** con `estado_gestion` de "gestionado" pero sin `fecha_primer_contacto`.
- **`historico_cierres.csv` NO es cruzable por `lead_id`** con `leads.csv`: usa namespace `HX-*`
  vs `LD-*`, intersección vacía. Es una cohorte separada, solo para calibrar y validar.

### Señal de negocio validada en el histórico

Velocidad del primer contacto es el predictor dominante y monotónico:

| Horas al primer contacto | Tasa de cierre |
|---|---|
| ≤1h | 15.1% |
| 1-4h | 11.6% |
| 4-24h | 7.8% |
| 24-48h | 7.7% |
| 48-120h | 5.8% |

Otras variables son señal débil (8-11%): `pidio_cita`, `manifesto_cuota_inicial`,
`forma_pago_declarada`. `numero_contactos` no muestra tendencia clara (ruido).
Tasa de cierre global sobre gestionados: 197/2.021 = **9.75%**.

## Decisión crítica de tenancy

El requisito obligatorio #8 exige que una comercializadora no vea datos de otra.
65% de los teléfonos duplicados cruzan empresas. Por eso:

> **La entidad `persona` se identifica por `(empresa_id, telefono_normalizado)`, nunca solo
> por teléfono.** Deduplicar globalmente fusionaría clientes de EMP-01 con EMP-03 y violaría
> la separación por empresa.

Impacto medido: dedup global daría 1.360 personas (140 colapsos) pero rompe tenancy;
dedup por empresa da **1.451 personas (49 colapsos legítimos)**.

## Convenciones de código

- Español para nombres de dominio (`lead`, `persona`, `asesor`, `puntaje`), inglés para
  términos técnicos estándar (`engine`, `session`, `upsert`).
- Los normalizadores en `src/normalizadores.py` son **funciones puras** sin I/O — se testean
  con pytest sin base de datos.
- Toda regla de negocio tiene un ID (`R1`..`R12`) documentado en `docs/reglas-normalizacion.md`
  y referenciado en el docstring de la función que la implementa.
- Nada de `print()` para logging: usar el módulo `logging`.
- Los datos crudos de `data/raw/` son sintéticos (lo dice el LEEME.txt) y **sí** se versionan,
  para que el evaluador pueda reproducir el pipeline. No hay PII real.

## Comandos

```bash
pip install -r requirements.txt

python -m src.schema              # regenera db/schema.sql (DDL PostgreSQL)
python pipeline.py --fase 1       # ingesta + normalización + dedup
python pipeline.py --fase 2       # extracción con IA (requiere ANTHROPIC_API_KEY en .env)
python pipeline.py --fase 2 --limite 20   # prueba de costo/calidad sobre una muestra
python pipeline.py                # pipeline completo (cuando estén todas las fases)

pytest -q                         # 86 tests: normalizadores + esquema + orquestación de IA
```

Base de datos por defecto: `sqlite:///data/warehouse.db` (override con `DATABASE_URL`).

## Fase 2 — extracción con IA (detalle de implementación)

- **Salida estructurada forzada**: `src/llm_cliente.py` usa `tool_choice` de la API de
  Anthropic (nunca pide JSON en texto libre). El esquema del tool se genera desde el
  mismo modelo Pydantic (`src/esquema_extraccion.py`) que valida la respuesta — una sola
  fuente de verdad para lo que el LLM puede devolver.
- **Dos puertas de validación**: esquema (Pydantic/enums) y semántica (rango plausible de
  `presupuesto_monto`). Ver regla **R13** en `docs/reglas-normalizacion.md`.
- **1 reintento, no más**: el esquema es fijo y conocido, así que no hay nada que
  replanificar. Si el reintento también falla, `extraccion_status='FALLO'` y el pipeline
  sigue — el lead queda priorizable sin enriquecimiento, nunca bloqueado.
- **`ClienteLLM` es un Protocol**, no una clase concreta: la lógica de negocio
  (`src/extraccion.py`) se testea con `ClienteLLMFalso` (en `tests/test_extraccion.py`)
  sin tocar la red ni necesitar una API key. `ClienteAnthropic` es la única pieza que
  habla con la API real.
- **Reanudable**: `_conversaciones_pendientes()` excluye las que ya tienen fila en
  `enriquecimiento_conversacion`, así que una corrida interrumpida se completa
  relanzando el mismo comando sin reprocesar (ni recobrar) lo ya hecho.
- **Concurrencia**: `ThreadPoolExecutor` (I/O-bound) para las ~665 conversaciones
  vinculadas; las escrituras a la base de datos ocurren después, en el hilo principal.
- **El LLM nunca resuelve el SKU**: devuelve `modelo_interes_texto` en texto libre: el
  mismo `EmparejadorModelos` de la Fase 1 (R6) lo resuelve a `sku_interes`, reutilizando
  la cascada ya probada contra el catálogo en vez de confiar en que el LLM no alucine un
  SKU inexistente.
- **Sin probar en vivo todavía**: no hay `ANTHROPIC_API_KEY` en este entorno de
  desarrollo. El código está cubierto por 22 tests con un cliente falso, pero la corrida
  real sobre las 665 conversaciones vinculadas (costo y calidad en producción) queda
  pendiente de que el usuario configure su llave y ejecute
  `python pipeline.py --fase 2 --limite 20` como prueba piloto antes del batch completo.

## Estado por fases

- [x] **Fase 0** — Modelo de datos, reglas de negocio, scaffolding
- [x] **Fase 1** — Ingesta + normalización + deduplicación
- [x] **Fase 2** — Extracción con IA desde conversaciones (código completo y testeado;
      pendiente de ejecución real con `ANTHROPIC_API_KEY` del usuario)
- [ ] **Fase 3** — Scoring y priorización (validado contra histórico)
- [ ] **Fase 4** — Persistencia final + asignación a asesores
- [ ] **Fase 5** — Automatización end-to-end
- [ ] **Fase 6** — Publicación (tablero)
- [ ] **Fase 7** — Documentación y entregables

## Reglas de trabajo

- **No** ejecutar fases futuras sin que el usuario apruebe el checkpoint anterior.
  Checkpoints definidos: CP1 (esquema+reglas), CP2 (lógica de scoring), CP3 (pipeline local completo).
- Commits incrementales reales — el enunciado descalifica un repo con un único commit final.
- Nunca commitear `.env`, llaves de API ni credenciales (condición descalificante del assessment).
