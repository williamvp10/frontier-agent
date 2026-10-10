# Arquitectura de Frontier Agent

Entrega aprobada v0.7. Tres vistas complementarias:

1. [Global de producción](arquitectura-produccion.md): API escalable, estado durable, pool de workers, gateways VNC y observabilidad.
2. [Demo en VPS](arquitectura-demo.md): misma separación lógica con infraestructura acotada y eventos locales.
3. [Multiagente](arquitectura-multiagente.md): orquestador, subgrafos y ciclo de ejecución/verificación.

[Documento de una página](diseno.md) · [Decisiones, escalabilidad y observabilidad](decisiones.md)

Cada diagrama dispone de su fuente `.mmd` para exportar. LangGraph orquesta; LangChain integra modelos y herramientas; LangSmith registra trazas y evaluaciones en la arquitectura de producción, con filtrado explícito.
