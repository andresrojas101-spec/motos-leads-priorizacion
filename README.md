# Priorización de leads — Motos y Motores del Norte S.A.S.

Pipeline automatizado que convierte leads crudos de tres canales en una lista priorizada
de gestión diaria por asesor, enriquecida con la información que hoy queda enterrada en
las conversaciones de WhatsApp.

Assessment técnico para el cargo de Analista de IA.

**Tablero público**: [motos-leads-priorizacion.streamlit.app](https://motos-leads-priorizacion.streamlit.app/)

## El problema

La comercializadora recibe más de 3.000 leads al mes por WhatsApp, campañas de Meta y el
formulario web. Todos caen en una sola bandeja y se atienden por orden de llegada. Cierra
menos de 1 de cada 10, y la información que define la prioridad —qué moto quiere, cuánta
cuota inicial tiene, si va por crédito— nunca llega al CRM.

El histórico de la propia empresa confirma que el orden importa:

| Horas hasta el primer contacto | Tasa de cierre |
|---|---|
| ≤ 1 h | **15.1 %** |
| 1 – 4 h | 11.6 % |
| 4 – 24 h | 7.8 % |
| 24 – 48 h | 7.7 % |
| 48 – 120 h | 5.8 % |

Un lead contactado en la primera hora cierra **2.6 veces más** que uno contactado dos días
después. Hoy, el 32 % de los leads no tiene registrado ningún primer contacto.

## Estado actual

| Fase | Entregable | Estado |
|---|---|---|
| 0 | Modelo de datos, reglas de negocio, scaffolding | ✅ |
| 1 | Ingesta + normalización + deduplicación | ✅ |
| 2 | Extracción con IA desde conversaciones | ✅ 665/665 procesadas (550 OK + 42 OK-tras-reintento + 32 fallo) |
| 3 | Scoring y priorización validados contra el histórico | ✅ (lift 2.89x validado; **CP2 aprobado**) |
| 4 | Persistencia final + asignación a asesores | ✅ 1.308 leads scoreados, 688 asignados hoy |
| 5 | Automatización end-to-end | ✅ GitHub Actions (cron diario + disparo manual), validado con corrida real |
| 6 | Publicación (tablero) | ✅ Streamlit Cloud, leyendo directo de Supabase |
| 7 | Documentación y entregables | ✅ README, diagrama, presentación (8 diapositivas) |

## Cómo se ejecuta

```bash
pip install -r requirements.txt
cp .env.example .env

# Extracción con IA: Ollama local por defecto (sin costo, sin llave, sin cuota diaria).
# Instalar Ollama (https://ollama.com) y descargar el modelo una vez:
ollama pull llama3.1:8b

python pipeline.py --fase 1               # ingesta + normalización + deduplicación
python pipeline.py --fase 2 --limite 20   # extracción con IA — probar en una muestra primero
python pipeline.py --fase 2               # extracción sobre las ~665 conversaciones vinculadas
python pipeline.py --fase 4               # scoring + asignación a asesores
python pipeline.py                        # las tres fases en secuencia (lo que corre GitHub Actions)

streamlit run tablero.py                  # tablero local, mismo código que en producción

pytest -q                                 # 153 tests
python -m src.schema                      # regenera db/schema.sql
```

El pipeline hace refresco completo en la Fase 1 (reconstruye el almacén desde
`data/raw/` en cada corrida, por lo que es idempotente) y refresco incremental en la
Fase 2 (solo procesa conversaciones sin extracción previa, por lo que es reanudable).

**Probar contra Postgres real en local** (no solo SQLite): `docker compose up -d` levanta
un Postgres con `docker-compose.yml`, y `DATABASE_URL=postgresql+psycopg://motos:motos@localhost:5432/motos_leads`
apunta el pipeline ahí. Así se encontró el bug de foreign keys descrito abajo, antes de
que importara en producción.

**Nota sobre la Fase 2**: el código está completo y cubierto por tests con un cliente LLM
falso (no requiere red). El proveedor de IA es intercambiable por variable de entorno
(`PROVEEDOR_LLM`, ver `.env.example`): **Ollama** local por defecto — sin costo, sin
llave, sin límite de tasa ni cuota diaria, el único techo es el hardware propio — con
Groq (capa gratuita en la nube) y Anthropic disponibles como alternativas documentadas.

Se empezó con Groq por no requerir hardware propio: piloto real sobre 20 conversaciones,
19/20 exitosas tras corregir dos bugs reales que solo aparecieron contra la API real (ver
"Decisiones tomadas" abajo). En el batch completo apareció un tercer límite — cuota de
200.000 tokens/día por modelo — que se agotó antes de terminar. Se migró a Ollama local
(`llama3.1:8b`) para el batch completo: mismo formato de tool-calling forzado, sin
límites de cuota. Resultado final sobre las 665 conversaciones: 550 OK al primer intento,
42 OK tras reintento, 32 fallo definitivo (89% con datos utilizables). Groq/Anthropic
quedan como alternativas ya probadas y documentadas, no se eliminó el código —
`ClienteLLM` es un `Protocol`, cambiar de proveedor es una variable de entorno.

## Resultado de la Fase 1

```
[LEADS]            1.503 crudos → 2 duplicados exactos → 1 rechazado → 1.500 válidos
[PERSONAS]         1.451 identidades  ·  49 leads secundarios consolidados
[MATCH MODELO]     1.312 de 1.421 textos resueltos a SKU (92.3 %)
[CONVERSACIONES]   677 cargadas (12 huérfanas)  ·  4.310 mensajes
[HISTÓRICO]        2.200 filas  ·  tasa de cierre 9.75 %
```

## Resultado de la Fase 4

Corrido contra el dataset completo de extracción (665/665) y los datos reales
(`python pipeline.py --fase 4`), con la fecha real de hoy como referencia:

```
[SCORING]      1.308 leads elegibles (excluye 149 descartados + 49 secundarios)
               21 CALIENTE · 143 TIBIO · 1.144 FRÍO
[ASIGNACIÓN]   688 de 1.308 asignados a un asesor hoy
               620 exceden la capacidad diaria combinada de su punto de venta
```

El sesgo hacia FRÍO no es un error: la mayoría de `leads.csv` se registró en agosto de
2026, así que evaluados "hoy" (mediados de septiembre) la mayoría ya lleva semanas sin
avance — exactamente el problema que describe el gerente. Con
`--fecha-referencia 2026-09-06` (más cerca de cuando esos leads eran nuevos) el balde
CALIENTE sube considerablemente, confirmando que el score responde correctamente al paso
del tiempo, no que esté mal calibrado.

Estos mismos números están replicados en Supabase (producción) — se verificaron
idénticos entre el SQLite local y Postgres tras correr la Fase 4 en ambos.

Los **620 leads que exceden la capacidad diaria** son, en sí mismos, la cuantificación
del reclamo original: *"estamos recibiendo más leads de los que alcanzamos a
gestionar."*

## Arquitectura

**Pipeline batch determinístico con un componente puntual de IA.** De los ocho requisitos
del alcance mínimo, siete son ingeniería de datos; solo la lectura de conversaciones
necesita un modelo de lenguaje.

```
data/raw/*.csv,json
        │
        ▼
  Normalización (R1-R6, R11, R12)  ──►  leads_rechazados
        │                               (nada se descarta en silencio)
        ▼
  Deduplicación por (empresa_id, teléfono)  ──►  personas
        │
        ▼
  Extracción LLM (tool-use forzado) ──► validación ──► reintento (1x) ──► fallo controlado   [Fase 2 ✅]
        │
        ▼
  Scorecard de reglas ponderadas, calibrado con historico_cierres   [Fase 3 ✅]
        │
        ▼
  Base de datos (lead_scores + asignaciones)   [Fase 4 ✅]
        │
        ▼
  GitHub Actions (cron diario + disparo manual)   [Fase 5 ✅]
        │
        ▼
  Tablero Streamlit "mis leads de hoy" filtrado por empresa   [Fase 6 ✅]
```

### Fase 5 — automatización (detalle)

Un solo trigger (`.github/workflows/pipeline.yml`) corre `python pipeline.py` completo:
cron diario o botón manual en la pestaña Actions. El runner instala Ollama y descarga el
modelo en cada corrida; como la Fase 2 es reanudable, solo la primera corrida contra una
base vacía toma horas — las siguientes son incrementales (en la práctica, minutos).

**Bug real encontrado validando contra Postgres real** (no solo SQLite, que nunca impuso
foreign keys): el refresco completo de la Fase 1 hacía `DROP TABLE` sobre
`leads`/`conversaciones`, y Postgres lo rechazaba porque `enriquecimiento_conversacion`/
`lead_scores`/`asignaciones` tienen FK hacia esas tablas. Como los ids son deterministas
desde los archivos crudos, el estado final siempre es consistente — el problema era solo
el chequeo de FK a mitad de transacción. Se resolvió marcando esas FK como `DEFERRABLE
INITIALLY DEFERRED` (se validan al `COMMIT`) y cambiando `DROP TABLE` por `DELETE` dentro
de la misma transacción que repuebla los datos. Validado corriendo la Fase 1 dos veces
seguidas contra Postgres con datos de Fase 2/4 ya poblados, sin perder ni corromper nada.

### Fase 6 — tablero público (detalle)

`tablero.py` (Streamlit) lee directo de la base — sin exportación ni snapshot intermedio.
Muestra, por empresa: KPIs de temperatura y capacidad, la bandeja de gestión del día por
asesor (filtrable por asesor/temperatura), y el desglose explicable del score de cada
lead (aporte de cada componente del scorecard).

**Simplificación deliberada**: selector de empresa en la UI en vez de login real. La
separación de datos SÍ está garantizada (toda query filtra por `empresa_id`, y la Fase 4
verificó 0 cruces reales entre asesores y leads de otra empresa) — lo que falta es
autenticación, ver "Qué haría con más tiempo".

### Fase 3 — scoring: metodología y validación

Scorecard de *weight of evidence* (log-odds vs. tasa base), la técnica clásica de credit
scoring — no un modelo entrenado. Metodología completa, tablas de evidencia y la
justificación de cada peso en [`docs/scoring.md`](docs/scoring.md).

- **Urgencia domina el score (45% del peso)**: horas desde el último avance real del lead
  (contacto si existe, si no el registro). Único componente siempre disponible.
- **Componentes secundarios validados** (forma de pago, cuota inicial, pidió cita) suman
  35% del peso — señal real pero débil, medida y documentada con su intervalo de
  confianza aproximado, no solo con el punto estimado.
- **Componentes de IA (20%)**: `intencion`/`objecion_principal` de la Fase 2 no tienen
  histórico equivalente — pesos razonados, no medidos, explícitamente marcados como
  supuesto a recalibrar con datos reales de esta app en producción.
- **Canal se midió y se descartó**: todas las tasas por canal caen dentro de ±1pp de la
  base — ruido, no señal. No entra a la fórmula.
- **Validación retro-activa** sobre los 2.021 leads gestionados del histórico
  (`python -m src.validar_scoring`):

| Temperatura | n | Tasa de cierre real |
|---|---|---|
| CALIENTE (score ≥65) | 467 | **16.06%** |
| TIBIO (40-64) | 942 | 9.34% |
| FRÍO (<40) | 612 | **5.56%** |

**Lift CALIENTE vs. FRÍO: 2.89x** — supera el 2.6x que ya daba la urgencia sola: los
componentes secundarios suman señal real. Esta validación (CP2) se hizo *antes* de
conectar `src/scoring.py` a la base de datos, a propósito: el plan original marcó este
punto como checkpoint para revisar la metodología antes de persistir a producción
(Fase 4), que ya está corriendo contra datos reales.

### Por qué no una arquitectura multi-agente

Descartada deliberadamente. Un sistema de agentes autónomos se justifica cuando hay
planeación dinámica, selección de herramientas en tiempo de ejecución o conversación
multi-turno impredecible. Aquí no hay nada de eso: el esquema de extracción es fijo y
conocido, el proceso es batch, y el score **debe** ser explicable y auditable — un
requisito que choca frontalmente con delegar la decisión al razonamiento de un LLM.

El LLM extrae atributos. **La última palabra sobre la prioridad siempre es del motor de
reglas.** Si la extracción falla o devuelve baja confianza, el lead sigue siendo
priorizable con menos información: la falla degrada el resultado, no bloquea el pipeline.

## Decisiones tomadas

**La identidad de cliente se llavea por `(empresa_id, teléfono)`, no solo por teléfono.**
Es la decisión más consecuente del proyecto. 141 teléfonos aparecen repetidos en el
dataset, y **91 de esos grupos (65 %) cruzan empresas distintas**. Deduplicar globalmente
daría 1.360 personas en vez de 1.451, pero fusionaría clientes de EMP-01 con EMP-03 y
violaría la separación por empresa que el enunciado exige como condición obligatoria. La
misma persona física comprando en dos comercializadoras del grupo es, para efectos de
datos y de cumplimiento, dos clientes distintos.

**Las fechas ambiguas se resuelven con una convención explícita y quedan marcadas.**
~480 fechas vienen como `DD/MM` o `MM/DD` con ambos componentes ≤ 12, lo que las hace
irresolubles: hay evidencia comprobada de *ambas* convenciones en la misma columna
(`23/08/2026` solo puede ser DD/MM; `08/18/2026` solo puede ser MM/DD). Se asume DD/MM
(estándar colombiano) y se guarda `fecha_registro_confianza = 'ambigua'`, para que el
scoring pueda degradar el peso de la antigüedad en esos leads.

**El matching de modelo prefiere no resolver antes que resolver mal.** `Bajaj Pulsar`
encaja en cuatro modelos del catálogo; asignarle uno sería inventar información que luego
contamina la verificación de inventario. En esos casos se conserva la marca y el SKU queda
nulo. La cascada de cinco pasos está en `docs/reglas-normalizacion.md` (R6).

**Ante un conflicto entre el estado declarado y la evidencia, gana la evidencia.** 86 leads
dicen estar `CONTACTADO` o `COTIZACIÓN ENVIADA` pero no tienen fecha de primer contacto.
Se tratan como no contactados y se marcan como inconsistentes. El error es asimétrico:
llamar de más a alguien ya contactado cuesta una llamada; dar por contactado a quien no lo
está lo condena a no recibir atención nunca — que es exactamente el problema a resolver.

**Nada se descarta en silencio.** El único lead rechazado (`LD-01501`: nombre
`"prueba prueba"`, teléfono `300123`, fecha `2026-08-33`) queda en `leads_rechazados` con
su motivo y su payload original. Las 12 conversaciones huérfanas se cargan marcadas como
tales en vez de borrarse.

**El proveedor de IA terminó siendo Ollama local, no Groq, por la misma restricción de
presupuesto que motivó elegir Groq en primer lugar.** Groq (capa gratuita, sin tarjeta)
fue la primera opción por no requerir hardware propio, pero su cuota diaria (200.000
tokens/modelo) resultó insuficiente para el batch completo — se agotó a mitad de camino
más de una vez, incluso alternando entre sus dos modelos gratuitos. Ollama local no tiene
cuota ni límite de tasa: el único techo es el hardware propio. La decisión no comprometió
la arquitectura: `ClienteLLM` se definió como un `Protocol` desde el primer commit de la
Fase 2, así que soportar un tercer proveedor fue añadir una clase (`ClienteOllama`) y
extender la fábrica (`construir_cliente_llm()`) — cero cambios en `extraccion.py` ni en
la lógica de negocio. Groq y Anthropic quedan disponibles como alternativas ya probadas y
documentadas, intercambiables por variable de entorno.

**La extracción con IA responde por `tool_choice` forzado, nunca por texto libre.** Pedirle
al modelo que "responda en JSON" en un prompt de texto es la forma menos confiable de
sacar salida estructurada — el modelo puede envolverla en markdown, agregar prosa antes o
saltarse un campo. Forzar una herramienta (`tool_choice={"type": "tool", ...}`) con el
esquema generado desde el mismo modelo Pydantic que valida la respuesta hace que la salida
sea estructuralmente correcta por construcción, no por suerte de prompting.

**Un solo reintento, no una cola de reintentos.** El esquema de extracción es fijo y
conocido de antemano — no hay nada que un agente deba "descubrir" reintentando. Si el
primer intento no cumple el esquema, un segundo intento con un prompt más estricto resuelve
la inmensa mayoría de los casos; más allá de eso, el problema probablemente no es de
formato sino de que la conversación es genuinamente ambigua, y seguir reintentando solo
añade costo sin mejorar el resultado. La conversación queda marcada `FALLO` y visible para
auditoría, en vez de reintentarse indefinidamente en silencio.

**Dos bugs reales encontrados en el piloto contra la API real, corregidos antes del batch
completo** — el motivo por el que se valida con datos reales y no solo con el cliente
falso de los tests:

1. El modelo Groq elegido inicialmente (`llama-3.3-70b-versatile`) ya no existía en su
   catálogo — 404 en el 100% de los intentos. El catálogo de Groq cambia con frecuencia;
   se corrigió a `openai/gpt-oss-20b`, verificado contra `client.models.list()`.
2. El prompt tenía una instrucción contradictoria: decía "si no hay información, usa null"
   para todos los campos, pero `intencion` y `objecion_principal` son enums obligatorios
   sin `null` en el esquema (para eso existe `NINGUNA`/`BAJA`). El modelo, siguiendo esa
   instrucción, inventaba valores como `"INTERESADO"` — 20/20 fallos de validación. Se
   corrigió enumerando los valores válidos explícitamente en el prompt, generados desde
   los mismos enums de Pydantic para que nunca se desincronicen del esquema real.

Tras ambos fixes: 19/20 exitosas. También se descubrió el límite real de la capa gratuita
(8.000 tokens/minuto por cuenta, no por conexión) y se bajó la concurrencia de 4 a 2
workers en consecuencia — más workers solo generaban más 429, no más throughput.

**Un tercer límite, distinto, apareció corriendo el batch completo (645 conversaciones):
una cuota de 200.000 tokens/DÍA, independiente de la de por minuto.** Una vez agotada,
cada conversación siguiente fallaba garantizado — el reintento de 2s no sirve contra un
límite que tarda horas en liberarse. El código ahora detecta este caso específicamente
(`CuotaAgotada`) y detiene el batch sin marcar las conversaciones restantes como
`FALLO` permanente: quedan "pendientes" para la próxima corrida, tal como estaban antes
de intentarse. Sin esto, 575 de 645 conversaciones quedaron marcadas `FALLO` cuando en
realidad nunca tuvieron una oportunidad real de extraerse — se limpiaron de la base real
tras el fix.

**La asignación reparte por round-robin de capacidad, no "primero al mejor asesor".**
`distribuir_leads()` recorre los asesores de un punto de venta en rotación, saltando a
los que ya llegaron a su tope. Un asesor con más capacidad queda disponible más turnos
en la rotación, así que termina con más leads sin necesitar una fórmula de reparto
proporcional explícita — y ningún asesor se queda mirando una bandeja vacía mientras
otro acapara todos los leads calientes.

**Un lead puede tener más de una conversación vinculada — el score usa la más
reciente.** 25 leads reales del dataset completo escribieron por WhatsApp más de una
vez. El join original entre `leads` y `enriquecimiento_conversacion` producía una fila
por conversación en vez de una por lead, y `persistir_scores` fallaba con
`UNIQUE constraint failed` al correr contra el dataset completo. Se deduplica quedándose
con la conversación de `fecha_inicio` más reciente por lead — el mismo criterio de
"último avance real" que ya usa el componente de urgencia — para que el score refleje el
estado más actual del cliente.

## Supuestos asumidos

- `canal` en `leads.csv` es el canal de **origen**; la conversación de WhatsApp es el canal
  de **atención**. Explica que las 677 conversaciones digan WhatsApp mientras solo el 55 %
  de sus leads lo diga en `leads.csv`. Se modelan como dos hechos coexistentes, sin forzar
  que coincidan. **A validar con el negocio.**
- Dos leads son la misma persona si comparten teléfono normalizado dentro de la misma
  empresa. No se exige además coincidencia de nombre.
- El catálogo es compartido a nivel de grupo: un mismo SKU aparece disponible en puntos de
  venta de las tres empresas. **A validar con el negocio.**
- `historico_cierres.csv` es una cohorte separada, no cruzable por `lead_id` con
  `leads.csv` (namespaces `HX-*` vs `LD-*`, intersección vacía). Se usa solo para calibrar
  y validar el scoring.
- Los datos de `data/raw/` son sintéticos, como indica el `LEEME.txt` del paquete, por lo
  que se versionan en el repositorio para que la ejecución sea reproducible. No hay PII real.

## Qué haría con más tiempo

- **Row Level Security nativo en Postgres** en lugar de filtrar por `empresa_id` en la capa
  de aplicación. Es la forma correcta de garantizar el aislamiento entre comercializadoras
  cuando esto deje de ser un demo.
- **Resolver las ambigüedades de fecha en el origen**, corrigiendo la captura en los tres
  canales en vez de adivinar aguas abajo.
- **Feedback loop del score**: registrar el desenlace real de los leads priorizados para
  recalibrar los pesos del scorecard con datos propios, en vez de solo con el histórico.
- **Un asistente conversacional para asesores** ("¿qué sé de este cliente?") — ese sí es un
  caso donde una arquitectura de agente con herramientas se justifica.

## Estructura

```
├── pipeline.py                   Punto de entrada (un solo disparo)
├── tablero.py                    Fase 6 — tablero público (Streamlit)
├── docker-compose.yml            Postgres local para validar antes de producción
├── .github/workflows/pipeline.yml  Fase 5 — cron diario + disparo manual
├── src/
│   ├── config.py                 Rutas, DATABASE_URL, umbrales, config del LLM
│   ├── schema.py                 Esquema (16 tablas) — fuente única de verdad
│   ├── normalizadores.py         R1-R6, R11, R12 — funciones puras
│   ├── ingesta.py                Carga de dimensiones, leads, conversaciones, histórico
│   ├── dedup.py                  R8 — identidad de persona
│   ├── calidad.py                Reporte y métricas de calidad
│   ├── esquema_extraccion.py     R13 — esquema Pydantic + validación semántica
│   ├── llm_cliente.py            R13 — clientes Ollama/Groq/Anthropic (tool-use forzado)
│   ├── extraccion.py             R13 — orquestación: reintento, fallo, batch concurrente
│   ├── scoring.py                Fase 3 — motor de scoring, funciones puras
│   ├── validar_scoring.py        Fase 3 — validación retro-activa contra el histórico
│   └── asignacion.py             Fase 4 — score → BD + reparto a asesores por capacidad
├── db/schema.sql                 DDL PostgreSQL generado y versionado
├── docs/
│   ├── modelo-datos.md           ERD y decisiones de modelado
│   ├── reglas-normalizacion.md   R1-R13 con evidencia del dataset
│   ├── scoring.md                Metodología del scorecard + validación (CP2)
│   └── diagrama-arquitectura.html  Diagrama explorable de la arquitectura
├── tests/                        153 tests sobre casos reales del dataset
└── data/raw/                     Archivos fuente (sintéticos)
```

## Base de datos

SQLAlchemy Core con `DATABASE_URL`: SQLite en local, **Postgres en Supabase en
producción** (proyecto real, no solo teórico), sin cambios en el código del pipeline. El
DDL vive versionado en `db/schema.sql` y se regenera desde `src/schema.py`, que es la
fuente única de verdad del modelo. `docker-compose.yml` levanta un Postgres local para
probar contra el motor real antes de tocar producción — así se encontró el bug de FK
descrito en la sección de Fase 5.
