# Priorización de leads — Motos y Motores del Norte S.A.S.

Pipeline automatizado que convierte leads crudos de tres canales en una lista priorizada
de gestión diaria por asesor, enriquecida con la información que hoy queda enterrada en
las conversaciones de WhatsApp.

Assessment técnico para el cargo de Analista de IA.

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
| 2 | Extracción con IA desde conversaciones | ⏳ |
| 3 | Scoring y priorización validados contra el histórico | ⏳ |
| 4 | Persistencia final + asignación a asesores | ⏳ |
| 5 | Automatización end-to-end | ⏳ |
| 6 | Publicación (tablero) | ⏳ |
| 7 | Documentación y entregables | ⏳ |

## Cómo se ejecuta

```bash
pip install -r requirements.txt
cp .env.example .env          # opcional: por defecto usa SQLite local

python pipeline.py --fase 1   # ingesta + normalización + deduplicación
pytest -q                     # 64 tests de los normalizadores
python -m src.schema          # regenera db/schema.sql
```

El pipeline hace refresco completo: cada corrida reconstruye el almacén desde
`data/raw/`, por lo que es idempotente y re-ejecutable sin efectos acumulativos.

## Resultado de la Fase 1

```
[LEADS]            1.503 crudos → 2 duplicados exactos → 1 rechazado → 1.500 válidos
[PERSONAS]         1.451 identidades  ·  49 leads secundarios consolidados
[MATCH MODELO]     1.312 de 1.421 textos resueltos a SKU (92.3 %)
[CONVERSACIONES]   677 cargadas (12 huérfanas)  ·  4.310 mensajes
[HISTÓRICO]        2.200 filas  ·  tasa de cierre 9.75 %
```

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
  Extracción LLM ──► validación de esquema ──► reintento ──► fallo controlado   [Fase 2]
        │
        ▼
  Scorecard de reglas ponderadas, calibrado con historico_cierres   [Fase 3]
        │
        ▼
  Base de datos  ──►  Tablero "mis leads de hoy" filtrado por empresa   [Fases 4-6]
```

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
├── src/
│   ├── config.py                 Rutas, DATABASE_URL, umbrales
│   ├── schema.py                 Esquema (16 tablas) — fuente única de verdad
│   ├── normalizadores.py         R1-R6, R11, R12 — funciones puras
│   ├── ingesta.py                Carga de dimensiones, leads, conversaciones, histórico
│   ├── dedup.py                  R8 — identidad de persona
│   └── calidad.py                Reporte y métricas de calidad
├── db/schema.sql                 DDL PostgreSQL generado y versionado
├── docs/
│   ├── modelo-datos.md           ERD y decisiones de modelado
│   └── reglas-normalizacion.md   R1-R12 con evidencia del dataset
├── tests/                        64 tests sobre casos reales del dataset
└── data/raw/                     Archivos fuente (sintéticos)
```

## Base de datos

SQLAlchemy Core con `DATABASE_URL`: SQLite en local, Postgres/Supabase en producción, sin
cambios en el código del pipeline. El DDL vive versionado en `db/schema.sql` y se regenera
desde `src/schema.py`, que es la fuente única de verdad del modelo.
