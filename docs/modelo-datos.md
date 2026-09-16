# Modelo de datos

## Principios de diseño

1. **`empresa_id` está presente en toda tabla con datos de cliente.** Es el eje de tenancy
   del requisito obligatorio #8. Ninguna consulta del tablero se ejecuta sin filtrar por él.
2. **Lo crudo se conserva junto a lo normalizado.** `telefono_raw` vive al lado de
   `persona.telefono_normalizado`. Permite auditar cualquier transformación y re-procesar
   sin volver al CSV.
3. **Nada se descarta en silencio.** Los leads rechazados van a `leads_rechazados` con su
   motivo; las conversaciones huérfanas se cargan marcadas, no se borran.
4. **La incertidumbre es un dato, no un detalle oculto.** Las columnas `*_confianza` y
   `metodo_match_modelo` permiten que el scoring degrade el peso de valores inciertos y que
   un analista audite las decisiones automáticas.
5. **El histórico vive aparte.** `historico_cierres` no tiene relación con `leads`: usa otro
   namespace de IDs (`HX-*` vs `LD-*`) e intersección vacía. Es una cohorte de calibración,
   no parte del flujo operativo.

## Diagrama

```mermaid
erDiagram
    empresas ||--o{ puntos_venta : "opera"
    empresas ||--o{ personas : "posee"
    empresas ||--o{ leads : "posee"
    puntos_venta ||--o{ asesores : "emplea"
    puntos_venta ||--o{ leads : "atiende"
    puntos_venta ||--o{ catalogo_disponibilidad : "tiene stock"
    catalogo_motos ||--o{ catalogo_disponibilidad : "disponible en"
    catalogo_motos ||--o{ leads : "modelo de interes"
    personas ||--o{ leads : "agrupa"
    leads ||--o| conversaciones : "vinculada"
    conversaciones ||--o{ mensajes : "contiene"
    conversaciones ||--o| enriquecimiento_conversacion : "extraccion IA"
    leads ||--o| lead_scores : "priorizado"
    leads ||--o{ asignaciones : "asignado"
    asesores ||--o{ asignaciones : "gestiona"
    ejecuciones ||--o{ metricas_calidad : "registra"

    empresas {
        text empresa_id PK
        text nombre
    }
    puntos_venta {
        text punto_venta_id PK
        text empresa_id FK
    }
    asesores {
        text asesor_id PK
        text nombre
        text punto_venta_id FK
        text empresa_id FK
        int capacidad_diaria_leads
        bool activo
        date fecha_ingreso
    }
    catalogo_motos {
        text sku PK
        text marca
        text linea
        text modelo_normalizado
        int cilindraje
        text segmento
        int precio_lista
        int unidades_disponibles
    }
    catalogo_disponibilidad {
        text sku PK_FK
        text punto_venta_id PK_FK
    }
    personas {
        int persona_id PK
        text empresa_id FK
        text telefono_normalizado
        text nombre_canonico
        text email_canonico
        text ciudad_normalizada
        int total_leads
    }
    leads {
        text lead_id PK
        int persona_id FK
        text empresa_id FK
        text punto_venta_id FK
        text canal_origen
        timestamp fecha_registro
        text fecha_registro_confianza
        timestamp fecha_primer_contacto
        text estado_gestion
        bool estado_inconsistente
        text sku_interes FK
        text marca_interes
        text metodo_match_modelo
        bool es_lead_canonico
    }
    leads_rechazados {
        int id PK
        text lead_id_declarado
        text motivo
        json payload
    }
    conversaciones {
        text conversacion_id PK
        text lead_id FK
        text empresa_id FK
        text canal
        timestamp fecha_inicio
        int num_mensajes
        text estado_vinculacion
    }
    mensajes {
        int mensaje_id PK
        text conversacion_id FK
        int orden
        text emisor
        text texto
    }
    historico_cierres {
        text lead_id PK
        text canal
        text empresa_id
        text sku FK
        float horas_al_primer_contacto
        int numero_contactos
        text forma_pago_declarada
        bool pidio_cita
        text desenlace
    }
    enriquecimiento_conversacion {
        text conversacion_id PK_FK
        text lead_id FK
        text sku_interes FK
        int presupuesto_monto
        text forma_pago
        text intencion
        text objecion_principal
        bool pidio_cita
        float confianza_global
        text extraccion_status
    }
    lead_scores {
        text lead_id PK_FK
        float score_total
        text temperatura
        json desglose
        text version_scoring
    }
    asignaciones {
        int id PK
        text lead_id FK
        text asesor_id FK
        date fecha_asignacion
        int orden_prioridad
    }
```

## Decisiones de modelado que vale la pena defender

### `personas` con llave `(empresa_id, telefono_normalizado)`

La decisión más consecuente del modelo. 65% de los teléfonos duplicados cruzan empresas;
una llave global de persona fusionaría clientes de comercializadoras distintas y violaría
la separación obligatoria. Detalle y cifras en `reglas-normalizacion.md`, regla **R8**.

### `catalogo_disponibilidad` como tabla propia

El CSV trae la disponibilidad como texto delimitado por `|`
(`PV-001|PV-003|PV-006|...`). Se normaliza a una tabla puente para poder responder con SQL
la pregunta que importa al scoring: *¿la moto que este cliente quiere está disponible en el
punto de venta que lo va a atender?* Un lead interesado en un modelo agotado en su punto de
venta no es igual de prioritario que uno con inventario disponible.

### `leads` conserva los no canónicos

Tras la deduplicación (R8), los leads secundarios no se borran: quedan con
`es_lead_canonico = false` apuntando a la misma persona. El tablero muestra solo canónicos,
pero se preserva la evidencia de que el cliente insistió por dos canales — que es en sí
misma una señal de interés que el scoring puede aprovechar.

### `enriquecimiento_conversacion` separada de `leads`

La extracción con IA vive en su propia tabla, con su `extraccion_status` y su confianza.
Si el LLM falla o devuelve baja confianza, `leads` sigue íntegra y el lead sigue siendo
priorizable con menor información. La falla del componente de IA degrada el resultado,
no lo bloquea.

### `lead_scores` versionada

`version_scoring` permite recalcular y comparar fórmulas sin perder el histórico de cómo se
priorizó ayer. Es lo que hace auditable la promesa de "lógica explicable": se puede
reconstruir por qué un lead quedó en el puesto 3 el martes pasado.
