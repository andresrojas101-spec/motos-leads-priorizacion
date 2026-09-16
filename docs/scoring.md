# Fase 3 — Scoring y priorización

**Estado: diseño para revisión (CP2). No integrado al pipeline de producción todavía.**

Este documento define la fórmula de priorización, su justificación con datos reales de
`historico_cierres.csv`, y su validación retro-activa. Es el checkpoint que el plan
original marcó como necesario antes de conectar el score a la base de datos y al tablero,
porque es la pieza más "IA/criterio" de la entrega (20/100 puntos) y la que exige "lógica
explicable y sustentada" según el enunciado.

## 1. Principio de diseño

**El score es un scorecard de reglas ponderadas, no un modelo entrenado.** El enunciado lo
permite explícitamente ("no se exige un modelo entrenado desde cero; se exige criterio y
justificación"), y dado que `historico_cierres.csv` tiene solo 2.200 filas con una tasa de
cierre del 9.75%, entrenar un clasificador desde cero sería sobre-ingeniería: pocos datos,
alto riesgo de sobreajuste, y — lo más importante — mucho menos explicable en una
sustentación de 10 minutos que una tabla de puntos con su tasa de cierre real al lado de
cada fila.

La técnica usada es la misma que un *credit scorecard* tradicional: cada categoría de cada
variable se convierte en una contribución basada en su **weight of evidence** (log-odds
frente a la tasa base), y las contribuciones se combinan en una suma ponderada. Es un
método de décadas de uso en scoring de riesgo, interpretable campo por campo, y no
requiere ningún framework de ML.

## 2. Qué se puede validar con el histórico y qué no

`historico_cierres.csv` no tiene las mismas columnas que produce la extracción de la Fase
2 (`intencion`, `objecion_principal` no existen ahí — son categorías que yo mismo diseñé
para el LLM). Esto divide el score en dos grupos honestos:

| Grupo | Componentes | Validación |
|---|---|---|
| **A — validado empíricamente** | urgencia (horas sin avance), forma de pago, cuota inicial declarada, pidió cita | Tasa de cierre real medida en 2.021 leads gestionados del histórico |
| **B — razonado, no validado** | intención (IA), objeción principal (IA), pidió cotización (IA) | Sin histórico equivalente. Peso deliberadamente menor, documentado como supuesto a recalibrar cuando haya desenlaces reales de leads scoreados por este sistema |

Esta distinción se refleja directamente en los pesos (sección 5): lo validado pesa más que
lo razonado.

## 3. Componente dominante: urgencia (antigüedad sin avance)

**Definición**: horas transcurridas desde `fecha_primer_contacto` si existe, o desde
`fecha_registro` si el lead nunca fue contactado. Se calcula al momento de correr el
scoring ("si contacto este lead ahora mismo, ¿en qué balde cae?").

**Por qué esta definición y no otra**: un lead contactado hace 5 días y sin resolución
está tan "enfriándose" como uno que nunca se tocó — ambos necesitan acción ahora. Usar
únicamente "leads sin contactar" dejaría fuera a los que se contactaron una vez y se
estancaron, que es parte del mismo problema que describe el gerente.

**Evidencia** (2.021 leads gestionados de `historico_cierres.csv`, tasa base 9.75%):

| Balde | n | Tasa de cierre | Log-odds vs. base |
|---|---|---|---|
| 0-1h | 377 | **15.12%** | +0.500 |
| 1-4h | 493 | 11.56% | +0.191 |
| 4-24h | 604 | 7.78% | −0.247 |
| 24-48h | 234 | 7.69% | −0.260 |
| 48-120h | 313 | 5.75% | −0.571 |
| >120h | — | *(sin datos; se extrapola la tendencia)* | −0.85 (supuesto) |

Diferencia estadísticamente sólida: el intervalo de confianza al 95% de 0-1h
(≈[11.5%, 18.7%]) no se solapa con el de 48-120h (≈[3.1%, 8.4%]). Es la única variable
del dataset con una tendencia monotónica limpia y un tamaño de efecto grande (2.6x entre
el mejor y el peor balde) — de ahí que reciba el peso dominante.

**Transformación a sub-score 0-100**: interpolación lineal entre el peor balde observado
(48-120h → 0) y el mejor (0-1h → 100). El balde >120h se satura en 0 (no se le da un
score negativo; ya es el peor caso posible).

## 4. Componentes secundarios (validados, señal débil)

### Forma de pago

| Valor | n | Tasa | Log-odds |
|---|---|---|---|
| contado | 347 | 11.82% | +0.195 |
| no_informa | 399 | 12.28% | +0.233 |
| crédito | 1.275 | 8.39% | −0.152 |

**Decisión de criterio, no solo de datos**: literalmente, `no_informa` tiene la tasa más
alta de las tres. No lo voy a usar para premiar al lead — es contraintuitivo (¿por qué
premiar no saber la forma de pago?), la muestra es más chica que la de crédito, y no hay
una explicación de negocio razonable detrás. Lo trato como ruido de muestreo, no como
señal. **Diseño final**: `contado` recibe un bono modesto (hay lógica de negocio real:
sin fricción de aprobación de crédito); `crédito` y `no_informa` quedan neutrales entre
sí. Este es exactamente el tipo de ajuste que el enunciado pide justificar con criterio,
no solo con el número crudo.

### Cuota inicial / presupuesto declarado

| Valor | n | Tasa | Log-odds |
|---|---|---|---|
| Sí manifestó | 822 | 11.80% | +0.187 |
| No manifestó | 687 | 8.73% | −0.114 |
| No informa | 512 | 7.81% | −0.243 |

Aquí sí hay lectura de negocio clara: declarar una cifra concreta de cuota inicial es un
compromiso más fuerte que decir "no tengo" o no decir nada. Se usa tal cual, interpolado
0-100 entre "No informa" (0) y "Sí" (100).

### Pidió cita

| Valor | n | Tasa | Log-odds |
|---|---|---|---|
| Sí | 594 | 11.78% | +0.169 |
| No | 1.427 | 8.90% | −0.070 |

Lift de ~32% (11.78% vs 8.90%). Señal razonable y business-sensible: pedir visitar el
punto de venta es una acción concreta de compra, no solo interés pasivo.

### Canal — descartado

| Canal | n | Tasa |
|---|---|---|
| WhatsApp | 1.101 | 10.35% |
| Formulario Web | 299 | 9.70% |
| Meta Ads | 621 | 8.70% |

Todas dentro de ±1 punto de la tasa base (9.75%), con intervalos de confianza que se
solapan ampliamente. **No entra al score.** Incluirlo con un peso simbólico solo para
"usar todas las variables" sería ruido disfrazado de señal — el ejercicio pide criterio,
y criterio incluye reconocer cuándo una variable no aporta.

## 5. Componentes de IA (Grupo B — razonados, sin validación empírica)

Provienen de `enriquecimiento_conversacion` (Fase 2) y solo existen para leads con
conversación vinculada y extracción exitosa (~44% del total). Sus pesos son deliberadamente
más chicos que los del Grupo A por no tener respaldo estadístico propio.

| Campo | Valores → sub-score | Justificación |
|---|---|---|
| `intencion` | ALTA→100, MEDIA→50, BAJA→0 | Graduación directa; es la lectura más cercana a "¿va a comprar?" que existe, aunque sin histórico que la calibre |
| `objecion_principal` | NINGUNA→100, CONSULTA_CON_TERCERO→50, OTRA→50, PRECIO→40, SIN_CUOTA_INICIAL→30, SOLO_COMPARANDO→20, BUSCA_USADO→10 | Orden de severidad razonado sobre los patrones observados en la exploración cualitativa de las conversaciones (objeción de precio es superable con alternativa; falta de producto usado es un mismatch que esta empresa no resuelve) |
| `pidio_cotizacion` | true→100, false→0 | Mismo tratamiento que "pidió cita": acción concreta hacia la compra |
| `forma_pago` (IA) | mismas categorías y pesos que la sección 4 | Diseñé el esquema de extracción con las mismas 3 categorías de `historico_cierres.csv` a propósito, para poder reusar el criterio ya validado |
| `presupuesto_monto` presente | presente→100, ausente→0 | Proxy directo de "manifestó cuota inicial" |

**Degradación por confianza**: cada sub-score de IA se ajusta hacia el punto neutral (50)
en proporción inversa a `confianza_global`:

```
sub_score_ajustado = 50 + confianza_global × (sub_score_crudo − 50)
```

Con confianza 1.0 no cambia nada; con confianza 0.3, un sub-score de 100 se convierte en
`50 + 0.3×50 = 65` — sigue sumando, pero mucho menos. Esto evita que una extracción dudosa
mueva el score con la misma fuerza que una confiable.

## 6. Fórmula final y pesos

```
score_total = Σ (peso_i × sub_score_i)  para los componentes disponibles,
              con los pesos renormalizados a 100% si falta alguno
```

| Componente | Peso (con enriquecimiento) | Grupo |
|---|---|---|
| Urgencia | 45% | A — validado, dominante |
| Intención (IA) | 20% | B — razonado |
| Forma de pago | 10% | A — validado |
| Cuota inicial / presupuesto | 10% | A — validado |
| Pidió cita / cotización | 10% | A — validado |
| Objeción principal (IA) | 5% | B — razonado |

**Sin enriquecimiento** (56% de los leads de `leads.csv`, los que no tienen conversación
vinculada o cuya extracción falló): el score es **100% urgencia**. No es una limitación
oculta — es honesto: sin conversación, no hay más información disponible para priorizar
que el tiempo transcurrido, que es exactamente el problema de negocio que describe el
gerente ("la información... nadie la pasa al CRM").

## 7. Temperatura y validación retro-activa

Cortes iniciales (a calibrar con el resultado de aplicar la fórmula al histórico):

- **CALIENTE**: score ≥ 65
- **TIBIO**: 40 ≤ score < 65
- **FRÍO**: score < 40

**Validación**: la misma fórmula (sin los componentes de IA, que no existen en el
histórico) se aplica a los 2.200 registros de `historico_cierres.csv`. Si la lógica de
priorización funciona, la tasa de cierre real de los leads que el score marca CALIENTE
debe ser sustancialmente mayor que la de los FRÍO — es literalmente la sugerencia del
enunciado: *"el histórico es la única fuente que le permite validar con datos si su
lógica de priorización realmente separa a los que cierran de los que no."*

## 8. Resultado de la validación retro-activa

Corrido con `python -m src.validar_scoring` sobre los 2.021 leads gestionados de
`historico_cierres.csv` (código en `src/validar_scoring.py`), usando `horas_al_primer_contacto`
del histórico como proxy de `horas_sin_avance` y sin componentes de IA (el histórico no
los tiene):

| Temperatura | n | Score promedio | Tasa de cierre real |
|---|---|---|---|
| CALIENTE (≥65) | 467 | 75.74 | **16.06%** |
| TIBIO (40-64) | 942 | 49.38 | 9.34% |
| FRÍO (<40) | 612 | 28.52 | **5.56%** |

**Lift CALIENTE vs. FRÍO: 2.89x** — superior al 2.6x que ya daba la urgencia sola,
confirmando que los componentes secundarios (forma de pago, cuota inicial, pidió cita)
suman señal real y no solo ruido. La vista por decil de score confirma la tendencia: los
dos deciles superiores cierran 16-17%, los tres inferiores 5-7%, con algo de ruido en los
deciles centrales — esperable dado que ahí es donde compiten señales débiles entre sí.

**Conclusión**: los cortes de temperatura (65/40) se mantienen sin ajuste — el resultado
en 3 baldes ya es monotónico y con separación clara. La fórmula queda validada para pasar
a producción (Fase 4), sujeta a la advertencia de la sección 2: el componente de IA (20%
del peso cuando hay enriquecimiento) sigue sin validación empírica propia y debería
recalibrarse una vez existan desenlaces reales de leads scoreados por este sistema — ver
"Qué haría con más tiempo" en el README.
