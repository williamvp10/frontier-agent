# Arquitectura multiagente

Versión 0.3 · Diseño propuesto.

```mermaid
flowchart TB
    start(["START · tarea aceptada"])
    init["Inicializar estado<br/>task_id · alcance · deadline · session_version"]
    observe["Observar pantalla<br/>Imagen · timestamp · observation_id"]
    orchestrator["Agente orquestador<br/>Plan · delegación · presupuesto"]
    route{"Ruta elegida"}
    operator["Subgrafo operador visual<br/>Descubrir controles · proponer acción"]
    verifier["Subgrafo verificador<br/>Identidad · alcance · resultado"]
    recovery["Subgrafo recuperador<br/>Diagnóstico · alternativa"]
    admission{"Validador determinista<br/>Sesión vigente y tiempo<br/>Acción dentro del alcance"}
    execute["Ejecutor VNC único<br/>Acción acotada · clic prolongado"]
    repaint["Esperar repintado y observar"]
    verified{"Resultado verificado"}
    budget{"Tiempo y reintentos disponibles"}
    fail["Preparar fallida<br/>Razón observada · sin respuesta inventada"]
    success["Preparar terminada<br/>Evidencia y respuesta requerida"]
    close["Revocar acciones · cancelar pendientes<br/>Desconectar y confirmar cierre"]
    summary["Validar resumen<br/>Formato · orden · sin secretos"]
    finish(["END · publicar resultado"])
    supervisor["Supervisor externo al razonamiento<br/>270 s: bloquear nuevas acciones<br/>Objetivo: cierre antes de 285 s"]
    pg["PostgreSQL<br/>Checkpoint por thread + namespace<br/>Eventos por task_id / run_id"]
    orchestrator <-->|"Estado durable"| pg
    operator -->|"Eventos estructurados"| pg
    verifier -->|"Eventos estructurados"| pg
    recovery -->|"Eventos estructurados"| pg
    execute -->|"Intención y resultado"| pg
    start --> init --> observe --> orchestrator --> route
    route -->|"Operar"| operator --> admission
    route -->|"Verificar"| verifier --> verified
    route -->|"Recuperar"| recovery --> budget
    route -->|"Ambigua / imposible"| fail
    admission -->|"Válida"| execute --> repaint --> orchestrator
    admission -->|"Inválida"| budget
    verified -->|"Sí"| success
    verified -->|"No"| budget
    budget -->|"Sí"| observe
    budget -->|"No"| fail
    success --> close
    fail --> close
    close --> summary --> finish
    supervisor -.->|"Revocar permisos de acción"| execute
    supervisor -.->|"Forzar cierre al vencer"| close
```

Vista lógica del grafo y sus subgrafos, con nodos deterministas separados de los agentes. Se visualizará como recorrido de ejecución y árbol de llamadas en herramientas del ecosistema LangGraph/LangSmith; este diagrama no es una captura de una ejecución real.

El orquestador mantiene memoria por tarea y entrega contexto mínimo a cada especialista. Operador, verificador y recuperador devuelven resultados estructurados al orquestador. Solo el ejecutor tiene acceso al adaptador. Las llamadas a modelos no implican servidores separados por subagente.

Los especialistas se invocan por hitos. El paralelismo se limita a análisis independientes de la misma observación; toda propuesta se revalida contra la versión actual antes de ejecutar. La política del controlador es necesaria, pero no puede demostrar mecánicamente todos los efectos de un clic en una app desconocida.

[Fuente Mermaid](arquitectura-multiagente.mmd)

[Persistencia, memoria y trazabilidad](persistencia.md): PostgreSQL desde el demo; ORM para entidades y adaptadores nativos para LangGraph.
