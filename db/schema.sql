-- Generado por `python -m src.schema`. No editar a mano.
-- Modelo de datos y justificación: docs/modelo-datos.md

CREATE TABLE catalogo_motos (
	sku VARCHAR(10) NOT NULL, 
	marca VARCHAR(40) NOT NULL, 
	linea VARCHAR(60) NOT NULL, 
	modelo_normalizado VARCHAR(100) NOT NULL, 
	cilindraje INTEGER, 
	segmento VARCHAR(40), 
	precio_lista INTEGER, 
	unidades_disponibles INTEGER, 
	PRIMARY KEY (sku)
);
CREATE INDEX ix_catalogo_motos_modelo_normalizado ON catalogo_motos (modelo_normalizado);

CREATE TABLE ejecuciones (
	run_id VARCHAR(40) NOT NULL, 
	fase VARCHAR(20) NOT NULL, 
	inicio TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
	fin TIMESTAMP WITHOUT TIME ZONE, 
	estado VARCHAR(15) NOT NULL, 
	PRIMARY KEY (run_id)
);

CREATE TABLE empresas (
	empresa_id VARCHAR(10) NOT NULL, 
	nombre VARCHAR(120) NOT NULL, 
	PRIMARY KEY (empresa_id)
);

CREATE TABLE leads_rechazados (
	id SERIAL NOT NULL, 
	lead_id_declarado VARCHAR(20), 
	motivo VARCHAR(40) NOT NULL, 
	detalle TEXT, 
	payload JSON, 
	fecha_ingesta TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (id)
);

CREATE TABLE historico_cierres (
	lead_id VARCHAR(20) NOT NULL, 
	fecha_registro DATE, 
	canal VARCHAR(20), 
	empresa_id VARCHAR(10), 
	punto_venta_id VARCHAR(10), 
	modelo_cotizado VARCHAR(100), 
	sku VARCHAR(10), 
	precio_lista INTEGER, 
	horas_al_primer_contacto FLOAT, 
	numero_contactos INTEGER, 
	manifesto_cuota_inicial VARCHAR(15), 
	forma_pago_declarada VARCHAR(15), 
	pidio_cita BOOLEAN, 
	desenlace VARCHAR(15), 
	PRIMARY KEY (lead_id), 
	FOREIGN KEY(sku) REFERENCES catalogo_motos (sku)
);

CREATE TABLE metricas_calidad (
	id SERIAL NOT NULL, 
	run_id VARCHAR(40) NOT NULL, 
	categoria VARCHAR(40) NOT NULL, 
	metrica VARCHAR(80) NOT NULL, 
	valor FLOAT, 
	detalle TEXT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(run_id) REFERENCES ejecuciones (run_id)
);
CREATE INDEX ix_metricas_calidad_run_id ON metricas_calidad (run_id);

CREATE TABLE personas (
	persona_id SERIAL NOT NULL, 
	empresa_id VARCHAR(10) NOT NULL, 
	telefono_normalizado VARCHAR(10) NOT NULL, 
	nombre_canonico VARCHAR(120), 
	email_canonico VARCHAR(150), 
	ciudad_normalizada VARCHAR(60), 
	total_leads INTEGER NOT NULL, 
	primer_registro TIMESTAMP WITHOUT TIME ZONE, 
	ultimo_registro TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (persona_id), 
	CONSTRAINT uq_persona_empresa_telefono UNIQUE (empresa_id, telefono_normalizado), 
	FOREIGN KEY(empresa_id) REFERENCES empresas (empresa_id)
);
CREATE INDEX ix_personas_empresa_id ON personas (empresa_id);

CREATE TABLE puntos_venta (
	punto_venta_id VARCHAR(10) NOT NULL, 
	empresa_id VARCHAR(10) NOT NULL, 
	PRIMARY KEY (punto_venta_id), 
	FOREIGN KEY(empresa_id) REFERENCES empresas (empresa_id)
);

CREATE TABLE asesores (
	asesor_id VARCHAR(10) NOT NULL, 
	nombre VARCHAR(120) NOT NULL, 
	punto_venta_id VARCHAR(10) NOT NULL, 
	empresa_id VARCHAR(10) NOT NULL, 
	capacidad_diaria_leads INTEGER NOT NULL, 
	activo BOOLEAN NOT NULL, 
	fecha_ingreso DATE, 
	PRIMARY KEY (asesor_id), 
	FOREIGN KEY(punto_venta_id) REFERENCES puntos_venta (punto_venta_id), 
	FOREIGN KEY(empresa_id) REFERENCES empresas (empresa_id)
);

CREATE TABLE catalogo_disponibilidad (
	sku VARCHAR(10) NOT NULL, 
	punto_venta_id VARCHAR(10) NOT NULL, 
	PRIMARY KEY (sku, punto_venta_id), 
	FOREIGN KEY(sku) REFERENCES catalogo_motos (sku), 
	FOREIGN KEY(punto_venta_id) REFERENCES puntos_venta (punto_venta_id)
);

CREATE TABLE leads (
	lead_id VARCHAR(20) NOT NULL, 
	persona_id INTEGER NOT NULL, 
	empresa_id VARCHAR(10) NOT NULL, 
	punto_venta_id VARCHAR(10) NOT NULL, 
	canal_origen VARCHAR(20) NOT NULL, 
	fecha_registro TIMESTAMP WITHOUT TIME ZONE, 
	fecha_registro_confianza VARCHAR(10), 
	fecha_primer_contacto TIMESTAMP WITHOUT TIME ZONE, 
	fecha_primer_contacto_confianza VARCHAR(10), 
	estado_gestion VARCHAR(25) NOT NULL, 
	estado_inconsistente BOOLEAN NOT NULL, 
	campania VARCHAR(80), 
	ciudad_normalizada VARCHAR(60), 
	sku_interes VARCHAR(10), 
	marca_interes VARCHAR(40), 
	metodo_match_modelo VARCHAR(25), 
	confianza_match_modelo FLOAT, 
	es_lead_canonico BOOLEAN NOT NULL, 
	nombre_raw VARCHAR(120), 
	telefono_raw VARCHAR(40), 
	email_raw VARCHAR(150), 
	ciudad_raw VARCHAR(60), 
	modelo_interes_texto VARCHAR(120), 
	fecha_ingesta TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (lead_id), 
	FOREIGN KEY(persona_id) REFERENCES personas (persona_id), 
	FOREIGN KEY(empresa_id) REFERENCES empresas (empresa_id), 
	FOREIGN KEY(punto_venta_id) REFERENCES puntos_venta (punto_venta_id), 
	FOREIGN KEY(sku_interes) REFERENCES catalogo_motos (sku)
);
CREATE INDEX ix_leads_empresa_id ON leads (empresa_id);
CREATE INDEX ix_leads_persona_id ON leads (persona_id);

CREATE TABLE asignaciones (
	id SERIAL NOT NULL, 
	lead_id VARCHAR(20) NOT NULL, 
	asesor_id VARCHAR(10) NOT NULL, 
	empresa_id VARCHAR(10) NOT NULL, 
	fecha_asignacion DATE NOT NULL, 
	orden_prioridad INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(lead_id) REFERENCES leads (lead_id), 
	FOREIGN KEY(asesor_id) REFERENCES asesores (asesor_id), 
	FOREIGN KEY(empresa_id) REFERENCES empresas (empresa_id)
);
CREATE INDEX ix_asignaciones_asesor_id ON asignaciones (asesor_id);
CREATE INDEX ix_asignaciones_lead_id ON asignaciones (lead_id);

CREATE TABLE conversaciones (
	conversacion_id VARCHAR(20) NOT NULL, 
	lead_id VARCHAR(20), 
	lead_id_declarado VARCHAR(20) NOT NULL, 
	empresa_id VARCHAR(10), 
	canal VARCHAR(20), 
	fecha_inicio TIMESTAMP WITHOUT TIME ZONE, 
	num_mensajes INTEGER, 
	estado_vinculacion VARCHAR(15) NOT NULL, 
	PRIMARY KEY (conversacion_id), 
	FOREIGN KEY(lead_id) REFERENCES leads (lead_id), 
	FOREIGN KEY(empresa_id) REFERENCES empresas (empresa_id)
);
CREATE INDEX ix_conversaciones_lead_id ON conversaciones (lead_id);
CREATE INDEX ix_conversaciones_empresa_id ON conversaciones (empresa_id);

CREATE TABLE lead_scores (
	lead_id VARCHAR(20) NOT NULL, 
	empresa_id VARCHAR(10) NOT NULL, 
	score_total FLOAT NOT NULL, 
	temperatura VARCHAR(10) NOT NULL, 
	desglose JSON, 
	version_scoring VARCHAR(20) NOT NULL, 
	fecha_calculo TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (lead_id), 
	FOREIGN KEY(lead_id) REFERENCES leads (lead_id), 
	FOREIGN KEY(empresa_id) REFERENCES empresas (empresa_id)
);
CREATE INDEX ix_lead_scores_empresa_id ON lead_scores (empresa_id);

CREATE TABLE enriquecimiento_conversacion (
	conversacion_id VARCHAR(20) NOT NULL, 
	lead_id VARCHAR(20), 
	modelo_interes_texto VARCHAR(120), 
	sku_interes VARCHAR(10), 
	presupuesto_monto INTEGER, 
	forma_pago VARCHAR(20), 
	intencion VARCHAR(20), 
	objecion_principal VARCHAR(40), 
	pidio_cita BOOLEAN, 
	pidio_cotizacion BOOLEAN, 
	confianza_global FLOAT, 
	extraccion_status VARCHAR(15) NOT NULL, 
	detalle_error TEXT, 
	modelo_llm VARCHAR(40), 
	fecha_extraccion TIMESTAMP WITHOUT TIME ZONE, 
	PRIMARY KEY (conversacion_id), 
	FOREIGN KEY(conversacion_id) REFERENCES conversaciones (conversacion_id), 
	FOREIGN KEY(lead_id) REFERENCES leads (lead_id), 
	FOREIGN KEY(sku_interes) REFERENCES catalogo_motos (sku)
);
CREATE INDEX ix_enriquecimiento_conversacion_lead_id ON enriquecimiento_conversacion (lead_id);

CREATE TABLE mensajes (
	mensaje_id SERIAL NOT NULL, 
	conversacion_id VARCHAR(20) NOT NULL, 
	orden INTEGER NOT NULL, 
	emisor VARCHAR(10) NOT NULL, 
	hora VARCHAR(5), 
	texto TEXT NOT NULL, 
	PRIMARY KEY (mensaje_id), 
	FOREIGN KEY(conversacion_id) REFERENCES conversaciones (conversacion_id)
);
CREATE INDEX ix_mensajes_conversacion_id ON mensajes (conversacion_id);
