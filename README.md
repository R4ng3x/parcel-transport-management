# Parcel Transport Management

Módulo para Odoo Community 19 orientado a empresas que gestionan envíos de uno o varios paquetes. Conserva la identidad visual de Pulse Ops inspirada en Mirror's Edge, pero sustituye el dominio de misiones por un flujo logístico completo y auditable.

## Alcance funcional

Cada `parcel.shipment` representa un envío y contiene uno o varios `parcel.package`, cada uno con un código público de seguimiento propio.

Flujo de estados:

```text
draft -> assigned -> partially_picked_up -> picked_up -> in_transit
                                                    -> partially_delivered -> delivered
                                                           |
in_transit ---------------- failure ------------------------+-> delivery_failed
partially_delivered -------- failure -----------------------+
delivery_failed ------------ retry -------------------------> in_transit
delivery_failed ------------ retry -------------------------> partially_delivered
```

La cancelación solo se permite antes de la primera entrega. Los estados parciales se derivan de eventos inmutables por paquete; nunca se eligen manualmente. Un fallo toma todos los paquetes recogidos que todavía no tienen entrega, libera al repartidor y sale de la reserva de capacidad. El reintento vuelve a `in_transit` o `partially_delivered` según exista ya alguna entrega, sin deshacerla.

Contratos principales:

- no se puede iniciar el transporte sin repartidor asignado;
- todos los paquetes deben estar recogidos antes de pasar a tránsito;
- un paquete solo puede recogerse y entregarse una vez;
- el peso es finito y positivo, usa unidades compatibles y respeta el máximo por paquete; cambiar el límite no puede invalidar envíos no terminales y la asignación vuelve a validarlo tras bloquear los paquetes;
- la capacidad del repartidor se limita simultáneamente por número de envíos y peso; `delivery_failed` no reserva capacidad y el reintento valida de nuevo ambos límites;
- recogida, inicio de tránsito, entrega y fallo comprueban ACL, reglas y autorización antes de bloquear, y repiten después la autorización ligada al repartidor mutable;
- las operaciones concurrentes respetan un orden global de bloqueo
  empresa/límite de paquete → envío → paquete → repartidor, comenzando en el
  primer nivel aplicable y sin invertir nunca la jerarquía;
- las direcciones y zonas se congelan en el envío para conservar su historial;
- los cambios de SLA, ruta y repartidor activo, además de cada intento fallido y su único reintento, quedan auditados con actor, fecha y motivo;
- los hechos `parcel.delivery.attempt` y `parcel.delivery.retry` son append-only, se crean solo desde acciones de dominio y no aceptan edición ni borrado;
- las escrituras directas de estado, repartidor, eventos y marcas temporales están bloqueadas;
- el tracking público mantiene las mismas claves y una cronología respaldada solo por marcas temporales de dominio: no sustituye una asignación sin fecha por `write_date` y nunca devuelve motivos, notas, identidades, IDs, direcciones ni paquetes hermanos.

## Arquitectura

```text
parcel.shipment
├── parcel.package (1..N, tracking público)
├── parcel.pickup.event (append-only)
├── parcel.delivery.event (append-only)
├── parcel.delivery.attempt (fallo append-only, paquetes pendientes)
├── parcel.delivery.retry (0..1 por intento, despacho append-only)
├── parcel.sla.revision (histórico)
├── parcel.route.correction (histórico)
└── parcel.courier.reassignment (histórico)

parcel.courier <-> parcel.delivery.zone <- parcel.zone.postcode.rule
```

La lógica reside en modelos Python. Las vistas, asistentes y el Command Center OWL llaman a métodos de dominio; no escriben campos operativos directamente.

- **Interfaz nativa:** listas, formularios, kanban no arrastrable, configuración, filtro dinámico de SLA vencido y asistentes de operación.
- **Command Center OWL:** red/panel operativo abstracto con cola de hasta 50 envíos, hasta 8 carriles origen-destino, hasta 8 zonas de presión destino, 50 repartidores y 8 actividades; refresco secuencial cada 60 segundos y conservación de la última instantánea válida ante errores.
- **Tracking público:** búsqueda por código globalmente único, controlador sin ACL ORM pública, DTO explícito, respuesta genérica para códigos inválidos y cabeceras `no-store`/`noindex`.
- **Documentos operativos:** manifiesto A4 del envío y etiqueta térmica de 100 × 150 mm por paquete con código de barras Code128; ambos respetan ACL y reglas multiempresa antes de renderizar.
- **Multiempresa:** reglas por compañías permitidas, relaciones validadas y límites configurables por empresa.

## Estrategia TDD

Se escribieron primero contratos observables para la lógica con riesgo real:

- máquina de estados y transiciones inválidas;
- requisito de repartidor y disponibilidad;
- generación, formato y unicidad de referencias y tracking;
- peso, UoM resuelta por empresa del envío, reconfiguración de límites, fechas, direcciones y resolución de zonas;
- capacidad dual y carreras por el último hueco o kilogramo;
- recogidas y entregas parciales, duplicadas o concurrentes;
- fallo total o tras entrega parcial, liberación de capacidad y restauración derivada al reintentar;
- rechazo atómico de motivos vacíos, estados inválidos y repartidores sin capacidad;
- dos envíos fallidos que compiten por el último hueco de reintento;
- cancelación frente a entrega concurrente;
- revisiones de SLA, correcciones de ruta y reasignación activa;
- permisos antes de los locks, aislamiento multiempresa, históricos append-only y contextos RPC falsificados;
- contrato exacto del dashboard, búsqueda dinámica de retrasos y privacidad HTTP del tracking, incluida la ausencia de hitos sin fecha fiable.

No se duplican con tests de bajo valor las garantías declarativas de Odoo: posición exacta de campos, colores, iconos, XML puramente visual ni CRUD básico. Esos aspectos se verifican instalando el módulo y recorriendo los escenarios de escritorio y móvil en navegador.

## Puesta en marcha

Requisitos: Podman, `podman-compose` y puertos 8069/5432 libres para los contenedores internos. El puerto web se publica únicamente en `127.0.0.1`.

```bash
cp .env.example .env
# Sustituye el valor de ODOO_MASTER_PASSWORD por un secreto largo y aleatorio.

podman compose run --rm odoo \
  --database parcel_transport \
  --load-language=en_US,es_ES \
  --init parcel_transport_management \
  --with-demo \
  --stop-after-init

podman compose up -d odoo
```

Omite `--with-demo` para una instalación sin escenarios de ejemplo.

Abrir:

```text
http://127.0.0.1:8069/web?db=parcel_transport
```

La configuración versionada no contiene la contraseña maestra. El entrypoint crea en tiempo de ejecución una copia privada de la configuración con `admin_passwd`, mantiene `list_db = False` y arranca Odoo con `odoo server --config ...`.

`db_name = parcel_transport` fija la base del servicio de larga duración, de modo que `/parcel/track` funciona sin cookie de selección aunque existan bases de prueba. Un `--database` explícito en `podman compose run` tiene prioridad sobre esa configuración; por eso las pruebas desechables siguen usando `parcel_transport_tdd`.

Tras instalar, asigna al usuario uno de los privilegios de **Parcel Transport**:

- **Courier:** consulta y opera únicamente sus envíos asignados;
- **Operator:** prepara envíos, asigna repartidores y registra operaciones;
- **Manager:** incluye operador y autoriza cancelaciones, revisiones y configuración.

### Idioma y actualizaciones

El arranque limpio anterior carga inglés y español mediante la opción exclusiva de CLI de Odoo 19 `--load-language=en_US,es_ES`; no se debe trasladar esa opción a `odoo.conf`. Cada usuario puede seleccionar español por la ruta normal **Avatar → My Preferences → Language**, elegir **Español / Spanish** y guardar.

Para activar español en una base `parcel_transport` existente y actualizar el módulo de forma reproducible:

```bash
podman compose stop odoo
podman compose run --rm odoo \
  --database parcel_transport \
  --load-language=en_US,es_ES \
  --update parcel_transport_management \
  --stop-after-init
podman compose up -d odoo
```

`--update parcel_transport_management` aplica conjuntamente el esquema Python, XML de datos y vistas, reglas de seguridad y catálogos de traducción del módulo. De forma predeterminada Odoo conserva traducciones modificadas directamente en la base. Si la intención es reemplazarlas por las versiones de los catálogos versionados, detén el servicio y repite la actualización añadiendo `--i18n-overwrite`:

```bash
podman compose run --rm odoo \
  --database parcel_transport \
  --load-language=en_US,es_ES \
  --update parcel_transport_management \
  --i18n-overwrite \
  --stop-after-init
podman compose up -d odoo
```

## Datos de demostración

La instalación con demo crea 8 envíos y 12 paquetes, además de zonas ficticias con nombres de ejemplo de Madrid y Barcelona, reglas postales por prefijo más específico y repartidores con capacidades distintas. Los nombres no representan geografía del Command Center. Los estados representativos son:

- borrador sin asignar;
- asignado a un repartidor posteriormente no disponible;
- recogida parcial;
- en tránsito;
- entrega parcial;
- entrega fallida, sin repartidor asignado y fuera de la capacidad reservada;
- SLA vencido;
- dirección sin cobertura.

Todas las transiciones demo pasan por `action_*`; no se precargan estados, enlaces ni históricos directamente.

Recorrido nativo: abre el envío fallido y señala statusbar, cinta **DELIVERY FAILED**, aviso e intento de solo lectura. Pulsa **Retry Delivery**, elige un repartidor —también puede volver a elegirse el anterior—, introduce un motivo y confirma; `parcel.assignment.wizard` llama a `action_retry_delivery`, restaura el estado derivado y vuelve a reservar capacidad. Audita el resultado en **Operations → History → Delivery Failure Events** y **Retry Audits**.

Para crear otro caso, parte de un envío en tránsito, pulsa **Record Delivery Failure** y revisa todos los paquetes pendientes calculados y de solo lectura en `parcel.delivery.failure.wizard`; tras confirmar el motivo, `action_record_delivery_failure` registra el hecho, libera al repartidor y deja el envío fuera de capacidad.

## Documentos operativos

Las acciones nativas de impresión completan el recorrido entre la planificación digital y la operación física:

- desde un envío, **Imprimir → Shipment Manifest** genera un manifiesto A4 con snapshots de recogida y entrega, repartidor, zonas, SLA, bultos, pesos y espacios de firma;
- desde uno o varios paquetes, **Imprimir → Package Label** genera una página térmica de 100 × 150 mm por bulto con tracking legible y Code128, referencia, peso y zona destino;
- la etiqueta omite nombres y direcciones para reducir exposición de datos cuando queda adherida al bulto.

Los modelos QWeb llaman a `check_access("read")` sobre los registros solicitados antes de entregar el contexto. El motor puede elevar la lectura de la configuración global de `ir.actions.report`, pero nunca renderiza envíos o paquetes fuera de las ACL y reglas de compañía del usuario.

## Pruebas

Ejecutar la batería del módulo en una base desechable:

```bash
podman compose run --rm odoo \
  --database parcel_transport_tdd \
  --init parcel_transport_management \
  --test-enable \
  --test-tags /parcel_transport_management \
  --stop-after-init \
  --log-level=test
```

Los tests están etiquetados `post_install` y `-at_install`. Incluyen transacciones independientes y barreras de hilos para comprobar contra PostgreSQL real las carreras de capacidad, entrega y dos envíos fallidos que compiten por un único hueco de reintento.

## Calidad del código

Las versiones de las herramientas están fijadas en `uv.lock` y `package-lock.json`. Para preparar el entorno de calidad se necesitan Node.js 20.19 o posterior, npm y `uv`:

```bash
npm ci
```

La puerta de calidad completa es no mutante y ejecuta Ruff sobre Python, ESLint con reglas Odoo/OWL y Prettier sobre JavaScript, SCSS, XML/QWeb, JSON, YAML y Markdown:

```bash
npm run quality
```

Para aplicar las correcciones seguras y el formato canónico:

```bash
npm run fix
```

Después de clonar el repositorio, el mismo control puede instalarse como hook local:

```bash
npm run hooks:install
```

Las excepciones de Ruff para `__init__.py` y `__manifest__.py` conservan respectivamente el orden de imports con efectos laterales de Odoo y el diccionario declarativo del manifiesto.

## Seguimiento público

Ruta:

```text
http://127.0.0.1:8069/parcel/track
```

El usuario consulta un paquete por su código `PTM-XXXX-XXXX-XXXX-XXXX`. La respuesta solo muestra estado, fechas operativas y cronología mínima del paquete consultado; no revela paquetes hermanos ni datos personales.
