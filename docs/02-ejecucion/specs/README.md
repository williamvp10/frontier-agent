# Especificación y plan de desarrollo

Versión 2 · 10 de octubre de 2026 · Estado: baseline integrado; evolución de contexto/hitos/Jev especificada y pendiente.

La entrega de Systems design v0.7 se conserva. Estos documentos concretan su ejecución; si una decisión cambia durante el incidente, registrar el cambio y su prueba de regresión aquí.

1. [Spec funcional y contratos internos](sistema.md): comportamiento normativo, estados, herramientas y multiagente.
2. [PostgreSQL](postgresql.md): entidades, transacciones, memoria y retención.
3. [DDL de referencia](schema.sql): estructura propuesta para traducir a SQLAlchemy/Alembic; no ejecutarla como sustituto de migraciones.
4. [Plan de trabajo y coordinación](planning.md): paquetes, dependencias, responsables y aceptación.
5. [Despliegue Dokploy](despliegue-dokploy.md): Docker Compose, servicios, réplicas y operación.
6. [Plan de pruebas](pruebas.md): casos verificables y evidencia requerida por paquete.
7. [Multiagente, contexto compartido y Jev](multiagente-contexto-jev.md): ejecución por hitos, memoria y mapa parcial, roles/tools, prompts en inglés y consultas completas a Jev como consejero; propuesta por implementar.

Fuente del contrato: material local del organizador `../../../../frontier-reto-1/README.md`, contrastado con [requisitos](../../requisitos-reto-1.md). Los fixtures originales permanecen fuera del repositorio público.

8. [Validación del contrato de entrypoint](contrato-entrypoint.md): forma pública, plazos, evidencia y pendientes de release.

Todos los modelos, incluido Jev, usan exclusivamente OpenRouter. Jev es un consejero con adaptador de decisiones separado; prompts y contenido normalizado de decisión en inglés. No traducir campos/estados públicos, instrucción original ni valores literales.

9. [Delegación de desarrollo](delegacion-desarrollo.md): encargo copiable para agente de apoyo, ownership, interfaces y gates del siguiente corte.
10. [Decisión de modelos y OpenRouter](decision-modelos-openrouter.md): asignación inicial por rol, precios de referencia, acceso exclusivo, routing, Jev y comparación pendiente.
11. [Evaluador de práctica por CLI](evaluador-practica-cli.md): plan y encargo para selección de casos, progreso en vivo, métricas y correlación Langfuse; pendiente de desarrollo.

## Definition of done

Cada paquete debe entregar implementación, pruebas asociadas pasando, comandos reproducibles y evidencia sanitizada. Marcar resultados reales en el planning; no convertir metas en resultados. Un cambio de interfaz necesita actualizar spec y consumidores antes de integrarse.

El siguiente corte es P04-C1→C6 del planning: contexto con procedencia, hitos, consejero Jev y pruebas. Reutilizar los contratos/API/persistencia existentes; no recrear scaffolds.

11. [Traspaso al agente integrador](../traspaso-desarrollo.md): estado verificado, variables por rol preparadas, integración pendiente y encargo vigente.
12. [Observabilidad Langfuse](../observabilidad-langfuse.md): implementación explícita,
    métricas seguras, tablero remoto y evidencia sintética; sustituye la previsión
    de LangSmith para observabilidad del runtime.
