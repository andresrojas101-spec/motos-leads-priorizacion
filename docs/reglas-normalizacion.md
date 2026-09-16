# Reglas de normalización y calidad de datos

Cada regla tiene un ID estable (`R1`..`R12`) referenciado desde el docstring de la función
que la implementa en `src/normalizadores.py` y `src/dedup.py`.

Principio transversal: **nunca se descarta información en silencio**. Todo registro
rechazado queda en `leads_rechazados` con su motivo, y todo valor que no se pudo resolver
con certeza queda marcado con un flag de confianza, no inventado.

---

## R1 — Teléfono

**Problema**: formatos mixtos — `3128183051`, `+57 350 7959623`, `(322) 315-6416`,
`320-637-4600`, `300 501 5843`.

**Regla**:
1. Eliminar todo carácter no numérico.
2. Si quedan 12 dígitos y empieza por `57`, quitar el indicativo de país.
3. Conservar los últimos 10 dígitos.
4. Validar contra `^3\d{9}$` (celular colombiano).
5. Si no valida → el lead se rechaza (`motivo='telefono_invalido'`).

**Justificación del paso 4**: el teléfono es la llave natural de identidad de persona
(R8). Un teléfono inválido hace imposible deduplicar y, en la práctica, hace imposible
gestionar el lead — un asesor no puede llamar a `300123`.

**Evidencia**: 1.170 filas de 10 dígitos, 332 con indicativo `57`, 1 inválida (`LD-01501`).

---

## R2 — Fechas

**Problema**: cuatro formatos conviviendo en la misma columna.

| Formato | `fecha_registro` | `fecha_primer_contacto` |
|---|---|---|
| `2026-08-21 21:33:00` | 593 | 200 |
| `23/08/2026 06:39` | 528 | 405 |
| `21-08-2026` | 219 | 208 |
| `2026-08-21T16:24:00` | 163 | 203 |

**Regla**: cascada de parseo con formatos explícitos, en este orden:
`%Y-%m-%d %H:%M:%S` → `%Y-%m-%dT%H:%M:%S` → `%d-%m-%Y` → formato con `/` (ver abajo) → fallo.

Para el formato con `/`, desambiguación por evidencia:
- Si el primer componente > 12 → es `DD/MM/YYYY`, confianza `exacta`.
- Si el segundo componente > 12 → es `MM/DD/YYYY`, confianza `exacta`.
- Si ambos ≤ 12 → **ambiguo**. Se asume `DD/MM/YYYY` (convención colombiana) y se marca
  confianza `ambigua`.
- Si el parseo produce una fecha inexistente (ej. `2026-08-33`) → el lead se rechaza
  (`motivo='fecha_invalida'`).

**Justificación**: la ambigüedad es irresoluble con la información disponible — no hay
ninguna otra señal en el registro que permita decidir. Asumir DD/MM es la opción de menor
error esperado en Colombia, pero la decisión queda **visible** en la columna
`fecha_registro_confianza`, para que el scoring pueda degradar el peso de la antigüedad
en esos leads y para que un analista pueda auditarlo después.

**Evidencia**: en `fecha_registro`, 204 filas son inequívocamente DD/MM, 59 inequívocamente
MM/DD y **265 son ambiguas**. En `fecha_primer_contacto`: 96 / 93 / **216**.
Que existan ambas convenciones comprobadas en la misma columna descarta la opción de
asumir un único formato para todas.

---

## R3 — Ciudad

**Problema**: hasta 6 variantes de escritura por ciudad.

**Regla**:
1. Normalizar: quitar acentos, mayúsculas, colapsar espacios, quitar espacios al borde.
2. Buscar en un diccionario explícito de alias → ciudad canónica (incluye abreviaturas
   locales como `B/QUILLA`, `STA MARTA`, `BOGOTA DC`).
3. Si no hay alias, fuzzy match contra las ciudades canónicas con umbral ≥ 90.
4. Si no supera el umbral → `ciudad_normalizada = NULL`, flag `ciudad_no_resuelta`.

El lead **no se rechaza** por ciudad no resuelta: la ciudad no es crítica para gestionar
el lead (el `punto_venta_id` ya determina la ubicación operativa).

**Ciudades canónicas**: Bogotá, Soacha, Medellín, Bello, Itagüí, Rionegro, Barranquilla,
Soledad, Cartagena, Santa Marta, Montería.

**Evidencia**: `Bogotá D.C.` / `Bogotá` / `Bogota` / `BOGOTA` / `bogotá` / `Bogota DC` son
la misma ciudad. `Rio Negro` y `Rionegro` también. `Medellín ` trae espacio final.

---

## R4 — Canal

**Problema**: 9 variantes para 3 valores reales.

**Regla**: normalizar (sin acentos, mayúsculas, sin espacios extra) y mapear al enum
`WHATSAPP` | `META_ADS` | `FORMULARIO_WEB`. Valor nulo o no reconocido → `DESCONOCIDO`.

Se almacena en `canal_origen` para dejar explícito que es el canal **de origen** del lead,
no el canal donde ocurrió la conversación (ver R10).

---

## R5 — Estado de gestión

**Problema**: 10 variantes para 6 estados.

**Regla**: normalizar y mapear al enum `SIN_GESTION` | `CONTACTADO` | `COTIZACION_ENVIADA` |
`EN_PROCESO` | `NO_CONTESTA` | `DESCARTADO`.

---

## R6 — Modelo de interés → SKU del catálogo

**Problema**: `modelo_interes_texto` es texto libre: 190 valores únicos para 24 SKU reales.
Incluye marca sola (`Bajaj`), errores de digitación (`Suzuky Gixxer 150`, `Hnda XR 150L`,
`A.K.T Dynamic R3 125`), nombres truncados (`Navi`, `GN 125`, `Bajaj Discover`) y sufijos
de año que no existen en el catálogo (`AKT Evo RS 150 2026`).

**Regla — cascada de 5 pasos**, en orden, con el primer acierto ganando:

| Paso | Condición | Resultado | Confianza |
|---|---|---|---|
| 1 | Texto normalizado == `marca + linea` del catálogo | SKU resuelto | 1.00 |
| 2 | Texto normalizado == una marca | `sku=NULL`, marca inferida | 0.50 |
| 3 | Tokens ⊆ tokens de **exactamente un** SKU | SKU resuelto | 0.90 |
| 4 | Fuzzy (`WRatio`) ≥ 88 contra un único mejor candidato | SKU resuelto | score/100 |
| 5 | Ninguno | `sku=NULL`, `marca=NULL` | 0.00 |

En el paso 3, si los tokens son subconjunto de **varios** SKU (ej. `Honda CB` → `CB 125F
Twister` y `CB 190R`), **no se adivina**: si todos los candidatos comparten marca se
conserva la marca con `sku=NULL`; si no, se deja sin resolver.

**Justificación**: un `token_set_ratio` simple daría 100% de cobertura pero con falsos
positivos graves — `Bajaj` (marca sola) haría match perfecto con cualquier Bajaj del
catálogo. Resolver a un SKU equivocado es peor que no resolver, porque el SKU alimenta la
verificación de disponibilidad de inventario en el punto de venta.

El método usado queda registrado en `metodo_match_modelo` para auditoría.

**Nota de precedencia**: cuando la Fase 2 extraiga el modelo desde la conversación, ese
valor tiene **mayor** prioridad que `modelo_interes_texto`, porque el cliente suele cambiar
de modelo durante el chat (observado: el asesor ofrece una alternativa más económica y el
cliente acepta).

---

## R7 — Duplicados exactos de fila

**Regla**: hash de la fila completa (todas las columnas crudas). Se conserva la primera
aparición, las siguientes se descartan contando el evento en el reporte de calidad.

**Evidencia**: `LD-00011` y `LD-00251` aparecen dos veces cada uno, con todos los campos
idénticos al segundo. Son duplicados de carga, no dos eventos reales.

---

## R8 — Identidad de persona y deduplicación multicanal

**Regla**: la llave natural de una persona es **`(empresa_id, telefono_normalizado)`**.

Dentro de cada empresa, los leads que comparten teléfono se agrupan en una `persona`.
Se elige un **lead canónico**:
1. El que tenga una conversación asociada (más contexto disponible).
2. Si hay empate o ninguno, el de `fecha_registro` más reciente.

Los demás leads del grupo se conservan (`es_lead_canonico = false`) apuntando a la misma
`persona`: no se borran, para no perder la trazabilidad de que el cliente tocó la puerta
por dos canales distintos.

El nombre canónico de la persona es el más completo del grupo (más tokens alfabéticos),
porque el dato viene abreviado en unos canales y completo en otros
(`J. Pérez Arias` vs `JULIÁN PÉREZ ARIAS`).

### Por qué la llave incluye `empresa_id`

Es la decisión de diseño más importante de la Fase 1. El requisito obligatorio #8 del
enunciado exige que una comercializadora no vea datos de otra. **91 de los 141 grupos de
teléfono duplicado (65%) cruzan empresas distintas.** Deduplicar solo por teléfono
fusionaría el cliente de EMP-01 con el de EMP-03 en un único registro, creando exactamente
la filtración que el requisito prohíbe.

| Estrategia | Personas | Colapsos | ¿Respeta tenancy? |
|---|---|---|---|
| Solo teléfono | 1.360 | 140 | **No** |
| `empresa_id` + teléfono | **1.451** | **49** | Sí |

Se elige la segunda. La misma persona física comprando en dos comercializadoras del grupo
es, para efectos de datos, dos clientes distintos — que es también el comportamiento
comercial y legalmente correcto.

---

## R9 — Estado de gestión vs. fecha de primer contacto

**Problema**: 86 leads declaran un estado que implica gestión (`CONTACTADO`,
`COTIZACION_ENVIADA`, `EN_PROCESO`, `NO_CONTESTA`, `DESCARTADO`) pero no tienen
`fecha_primer_contacto`.

**Regla**: la **fecha manda**. Si no hay fecha de primer contacto, el lead se trata como
no contactado para efectos de priorización, y se marca `estado_inconsistente = true`.

**Justificación**: `estado_gestion` ya demostró ser un campo poco confiable (10 variantes
de escritura para 6 estados). La ausencia de un timestamp es evidencia más dura que un
string escrito a mano. Además, el error es asimétrico: tratar como pendiente un lead ya
contactado cuesta una llamada redundante; tratar como contactado un lead pendiente lo
condena a no recibir nunca atención — que es precisamente el problema que este proyecto
existe para resolver.

---

## R10 — Conversaciones y vinculación con leads

**Regla**:
- Las conversaciones se cargan **siempre**, junto con sus mensajes normalizados.
- Si su `lead_id` existe en `leads` → `estado_vinculacion = 'VINCULADA'`.
- Si no existe → `estado_vinculacion = 'HUERFANA'`, `lead_id = NULL`. Se conserva el
  `lead_id_declarado` original para auditoría. Estas quedan excluidas de la extracción con
  IA de la Fase 2 (no hay lead al cual enriquecer) y no se inventan leads nuevos.

**Evidencia**: 12 conversaciones referencian IDs del rango `LD-9xxxx` que no existen en
`leads.csv` (rango real: `LD-00001`..`LD-01501`).

### Canal de origen vs. canal de atención

Las 677 conversaciones están etiquetadas `canal="WhatsApp"`, pero al cruzarlas con
`leads.csv` solo el 55% de esos leads dice WhatsApp: 186 dicen Meta Ads y 113 Formulario Web.

**No se fuerza la coincidencia.** Se modelan como dos hechos distintos y coexistentes:
`leads.canal_origen` (cómo entró el lead) y `conversaciones.canal` (dónde se conversó).
Es el comportamiento omnicanal esperable — el call center contacta por WhatsApp sin
importar por dónde entró el lead. Queda como pregunta abierta a validar con el negocio.

---

## R11 — Email

**Regla**: minúsculas, sin espacios al borde, validación básica de patrón. Si no valida →
`NULL`. No causa rechazo del lead (el 47% de los leads no tiene email y aun así son
gestionables por teléfono).

---

## R12 — Nombre del cliente

**Regla**: se conserva el crudo y se genera una versión normalizada en Title Case
(`JULIÁN PÉREZ ARIAS` → `Julián Pérez Arias`), preservando acentos. Se usa para elegir el
nombre canónico de la persona en R8.

---

## Resumen de destino de cada anomalía detectada

| Anomalía | Destino |
|---|---|
| Fila de prueba `LD-01501` (fecha día 33, teléfono 6 dígitos) | `leads_rechazados` (R1 + R2) |
| Duplicados exactos `LD-00011`, `LD-00251` | Descartados por hash (R7) |
| 141 teléfonos repetidos | Agrupados en `personas` por empresa (R8) |
| 4 formatos de fecha, ~480 ambiguas | Parseadas con flag de confianza (R2) |
| Ciudades con 6 variantes | Diccionario de alias + fuzzy (R3) |
| `canal` / `estado_gestion` con casing mixto | Mapeo a enum (R4, R5) |
| 190 textos de modelo para 24 SKU | Cascada de 5 pasos (R6) |
| 12 conversaciones huérfanas | Cargadas como `HUERFANA` (R10) |
| 86 estados inconsistentes | Flag `estado_inconsistente`, la fecha manda (R9) |
| Discrepancia de canal 44% | Dos columnas distintas, sin forzar (R10) |
