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
| Proveedor LLM (Fase 2) | **Groq** por defecto (capa gratuita, sin tarjeta); Anthropic como alternativa | El proyecto no tiene presupuesto para APIs de pago (decisión del usuario). `ClienteLLM` es un `Protocol`, así que el cambio de proveedor no tocó `extraccion.py` ni sus tests — ver `PROVEEDOR_LLM` en `.env` |

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
python pipeline.py --fase 2       # extracción con IA (requiere GROQ_API_KEY en .env)
python pipeline.py --fase 2 --limite 20   # prueba de costo/calidad sobre una muestra
python pipeline.py                # pipeline completo (cuando estén todas las fases)

pytest -q                         # 90 tests: normalizadores + esquema + orquestación de IA
```

Base de datos por defecto: `sqlite:///data/warehouse.db` (override con `DATABASE_URL`).

## Fase 2 — extracción con IA (detalle de implementación)

- **Proveedor: Groq por defecto, no Anthropic**. Restricción real de presupuesto — el
  proyecto no paga por APIs. `GROQ_API_KEY` en `.env` (capa gratuita, sin tarjeta,
  console.groq.com). `PROVEEDOR_LLM=anthropic` + `ANTHROPIC_API_KEY` sigue disponible como
  alternativa si en algún momento se prioriza calidad sobre costo — es una variable de
  entorno, no un cambio de código.
- **Salida estructurada forzada**: `src/llm_cliente.py` fuerza tool-calling en ambos
  proveedores (`tool_choice` en Anthropic, formato OpenAI-compatible en Groq), nunca pide
  JSON en texto libre. El esquema del tool se genera desde el mismo modelo Pydantic
  (`src/esquema_extraccion.py`) que valida la respuesta — una sola fuente de verdad.
- **Dos puertas de validación**: esquema (Pydantic/enums) y semántica (rango plausible de
  `presupuesto_monto`). Ver regla **R13** en `docs/reglas-normalizacion.md`.
- **1 reintento, no más**: el esquema es fijo y conocido, así que no hay nada que
  replanificar. Si el reintento también falla, `extraccion_status='FALLO'` y el pipeline
  sigue — el lead queda priorizable sin enriquecimiento, nunca bloqueado. Entre intento y
  reintento hay una pausa corta (`ESPERA_ENTRE_INTENTOS_SEGUNDOS`) pensada para absorber
  los 429 de límite de tasa de la capa gratuita, no solo fallas de formato.
- **`ClienteLLM` es un Protocol**, no una clase concreta: la lógica de negocio
  (`src/extraccion.py`) se testea con `ClienteLLMFalso` (en `tests/test_extraccion.py`)
  sin tocar la red ni necesitar una API key. `ClienteGroq` y `ClienteAnthropic` son las
  únicas piezas que hablan con una API real; `construir_cliente_llm()` en
  `llm_cliente.py` elige cuál instanciar según `PROVEEDOR_LLM`.
- **Reanudable**: `_conversaciones_pendientes()` excluye las que ya tienen fila en
  `enriquecimiento_conversacion`, así que una corrida interrumpida se completa
  relanzando el mismo comando sin reprocesar (ni recobrar) lo ya hecho.
- **Concurrencia conservadora**: `ThreadPoolExecutor` con `EXTRACCION_MAX_WORKERS=2` por
  defecto. Verificado con piloto real, no es una suposición: el límite gratuito de Groq
  para `openai/gpt-oss-20b` es **8.000 tokens/minuto a nivel de cuenta**, no por conexión,
  y cada extracción consume ~1.200-1.900 tokens — el techo real es ~5 peticiones/minuto
  sin importar cuántos workers corran. Más concurrencia solo generaba más 429
  desperdiciados, no más throughput.
- **El LLM nunca resuelve el SKU**: devuelve `modelo_interes_texto` en texto libre: el
  mismo `EmparejadorModelos` de la Fase 1 (R6) lo resuelve a `sku_interes`, reutilizando
  la cascada ya probada contra el catálogo en vez de confiar en que el LLM no alucine un
  SKU inexistente.

### Piloto real ejecutado (20 conversaciones, `openai/gpt-oss-20b` vía Groq)

Dos bugs reales encontrados y corregidos en el camino (no hipotéticos — aparecieron al
correr contra la API real, exactamente el tipo de cosa que un cliente falso no puede
atrapar):

1. **`llama-3.3-70b-versatile` (el modelo por defecto original) ya no existe** en el
   catálogo de Groq — 404 en el 100% de los intentos. El catálogo de Groq cambia con
   frecuencia; se corrigió el default a `openai/gpt-oss-20b` (verificado contra
   `client.models.list()` con la key real) y se documentó en `.env.example` cómo
   verificarlo si vuelve a pasar.
2. **Bug propio en el prompt**: instruía "si no hay información, usa null" para *todos*
   los campos, pero `intencion` y `objecion_principal` son enums obligatorios sin `null`
   en el esquema. El modelo, siguiendo la instrucción contradictoria, inventaba valores
   como `"INTERESADO"` (20/20 fallos de validación). Corregido enumerando los valores
   válidos explícitamente en el prompt, generados desde los mismos enums de Pydantic.

Resultado final tras ambos fixes: **19/20 exitosas** (18 al primer intento, 1 tras
reintento), confianza promedio 0.876. Verificación cualitativa manual de 4 casos contra
su transcripción original: parseo correcto de cifras coloquiales (`"2000mil"` →
2.000.000, `"3000mil"` → 3.000.000) y seguimiento correcto de cambio de modelo dentro de
la conversación. Un matiz de calibración detectado, no bloqueante: en una conversación de
"solo estoy comparando" (patrón de baja intención identificado en la exploración inicial)
el modelo clasificó `intencion=MEDIA` en vez de `BAJA` — vale la pena vigilar en el batch
completo, no amerita rediseño.

**Economía real de la capa gratuita**: al ritmo sostenible de ~5 peticiones/minuto, correr
las ~665 conversaciones vinculadas toma aproximadamente **2 a 2.5 horas**, no minutos.
Esto es información real para el diseño de la Fase 5 (automatización): el job en GitHub
Actions necesita un timeout generoso, o el batch debe poder correr en background /
reanudarse entre ejecuciones (ya lo soporta, por ser reanudable por diseño).

**Persistencia incremental (fix posterior)**: el batch original no escribía nada hasta
terminar las 645 conversaciones restantes — cualquier interrupción en esas ~2h perdía
todo. `ejecutar_extraccion` ahora hace `INSERT` + `commit()` cada `TAMANO_LOTE_COMMIT=25`
conversaciones; `pipeline.py` usa `engine.connect()` en vez de `engine.begin()` para Fase
2 específicamente (Fase 1 conserva `.begin()` a propósito: ahí la atomicidad del refresco
completo sí es una feature). Test que prueba la propiedad real (no solo el mecanismo):
simula un fallo externo a mitad de un batch y verifica que el lote ya commiteado
sobrevive.

## Fase 3 — scoring y priorización (detalle de implementación)

Metodología completa, tablas de evidencia y validación en `docs/scoring.md` — léelo antes
de tocar `src/scoring.py`, no repetir el razonamiento aquí.

- **Técnica**: scorecard de *weight of evidence* (log-odds vs. tasa base), la misma
  técnica de credit scoring de décadas de uso — no un modelo entrenado. El enunciado lo
  permite explícitamente y es muchísimo más explicable en una sustentación de 10 minutos.
- **Componente dominante (45% del peso): urgencia** — horas desde el último avance real
  (`fecha_primer_contacto` si existe, si no `fecha_registro`). Único componente que
  **siempre** está disponible (toda fila tiene fecha de registro); el resto depende de
  tener conversación enriquecida.
- **Renormalización dinámica**: si falta un componente (56% de los leads no tienen
  conversación vinculada), se excluye del cálculo y los pesos restantes se renormalizan a
  1.0 — nunca se imputa un valor neutro fingiendo tener información que no existe. Sin
  enriquecimiento, el score es 100% urgencia.
- **Canal se midió y se descartó** — todas las tasas de cierre por canal caen dentro de
  ±1pp de la base, estadísticamente indistinguible de ruido. No entra a la fórmula.
  Ejemplo deliberado de "criterio" que pide el enunciado: no todo lo medido merece peso.
- **Decisión de criterio sobre dato contraintuitivo**: `forma_pago=no_informa` tiene la
  tasa de cierre MÁS ALTA en el histórico (12.28%, por encima de `contado` con 11.82%).
  Se decidió NO premiarlo — es contraintuitivo y estadísticamente ruidoso (muestra chica,
  sin explicación de negocio plausible) — en vez de seguir el dato crudo a ciegas.
- **Componentes de IA (20% del peso, Grupo B)**: `intencion`/`objecion_principal` de la
  Fase 2 no existen en `historico_cierres.csv` — no hay forma de validarlos
  empíricamente. Pesos razonados, no medidos, explícitamente documentados como supuesto a
  recalibrar cuando existan desenlaces reales.
- **Degradación por confianza**: los sub-scores de IA se acercan al punto neutral (50) en
  proporción a `confianza_global` del LLM — una extracción de confianza 0.3 pesa mucho
  menos que una de 1.0, sin llegar a anularse del todo.
- **Validación retro-activa** (`python -m src.validar_scoring`, sobre los 2.021 leads
  gestionados del histórico): **CALIENTE cierra 16.06%, TIBIO 9.34%, FRÍO 5.56% — lift de
  2.89x**, superando el 2.6x que ya daba la urgencia sola. Confirma que los componentes
  secundarios suman señal real. Cortes de temperatura (65/40) sin ajustar: el resultado
  en 3 baldes ya salió monotónico.
- **Aún no integrado a producción**: `src/scoring.py` existe y está validado, pero no se
  ha conectado a `leads` ni a la tabla `lead_scores` — eso es Fase 4 (CP2 del plan
  original: revisar la lógica de scoring antes de persistirla). Pendiente de aprobación
  del usuario antes de avanzar.

## Estado por fases

- [x] **Fase 0** — Modelo de datos, reglas de negocio, scaffolding
- [x] **Fase 1** — Ingesta + normalización + deduplicación
- [x] **Fase 2** — Extracción con IA desde conversaciones (código completo, testeado, y
      validado con piloto real contra Groq: 19/20 exitosas, más el fix de persistencia
      incremental. Batch completo de ~665 conversaciones corriendo en background)
- [x] **Fase 3** — Scoring y priorización: motor implementado, validado contra histórico
      (lift 2.89x CALIENTE/FRÍO). **CP2 pendiente de aprobación del usuario** antes de
      persistir a producción (Fase 4)
- [ ] **Fase 4** — Persistencia final + asignación a asesores
- [ ] **Fase 5** — Automatización end-to-end
- [ ] **Fase 6** — Publicación (tablero)
- [ ] **Fase 7** — Documentación y entregables

## Reglas de trabajo

- **No** ejecutar fases futuras sin que el usuario apruebe el checkpoint anterior.
  Checkpoints definidos: CP1 (esquema+reglas), CP2 (lógica de scoring), CP3 (pipeline local completo).
- Commits incrementales reales — el enunciado descalifica un repo con un único commit final.
- Nunca commitear `.env`, llaves de API ni credenciales (condición descalificante del assessment).
