# Arquitectura del demo en VPS

Versión 0.3 · Diseño propuesto.

```mermaid
flowchart TB
    evaluator["Evaluador"]
    subgraph vps["Una VPS Linux · Docker Compose"]
        proxy["Proxy HTTPS"]
        api["FastAPI<br/>Bearer auth · health · tasks"]
        redis["Redis privado<br/>Cola · coordinación"]
        db["PostgreSQL privado<br/>Tareas · eventos · checkpoints · memoria"]
        dispatcher["Dispatcher outbox<br/>Publicación durable a cola"]
        secrets["Credenciales efímeras<br/>Fuera del contexto del modelo"]
        subgraph worker["Worker · concurrencia acotada"]
            agentflow["LangGraph + LangChain<br/>Orquestador y subagentes"]
            gate["Ejecutor único<br/>Sesión · plazo · acción"]
            vnc["Adaptador VNC directo<br/>AsyncVNC + puente WSS"]
            timer["Supervisor independiente del modelo"]
        end
        files["Volumen privado<br/>Eventos locales y capturas"]
    end
    model["Modelo visual permitido"]
    desktop["Escritorio recibido en POST /tasks"]
    evaluator --> proxy --> api
    api <-->|"SQLAlchemy async · tareas/resultados"| db
    db -->|"Outbox pendiente"| dispatcher --> redis
    api --> secrets
    redis --> agentflow
    agentflow <-->|"Imagen y contexto sin credenciales"| model
    agentflow -->|"Acción propuesta"| gate --> vnc
    vnc -->|"Observación"| agentflow
    secrets --> vnc
    vnc <-->|"WSS / RFB"| desktop
    db -->|"Deadline desde recepción"| timer
    timer -.->|"Revocar"| gate
    timer -.->|"Cancelar"| agentflow
    timer -.->|"Desconectar"| vnc
    agentflow -->|"Solicitar cierre"| timer
    timer -->|"Cierre confirmado"| agentflow
    agentflow <-->|"Saver/Store nativos LangGraph"| db
    agentflow -->|"Eventos y resultado · ORM"| db
    agentflow --> files
    vnc --> files
    api -->|"task_id / estado y resumen"| evaluator
```

Despliegue previsto para la hackathon: una VPS, API y worker separados, Redis, PostgreSQL y volumen local. No requiere Chromium, Playwright, Kubernetes ni LangSmith. LangSmith permanece deshabilitado; sí se registran eventos locales sin secretos.

El transporte VNC directo está validado en el notebook; la API y el grafo todavía deben implementarse. Comenzar con capacidad medida y ampliar slots solo para escritorios independientes. La VPS es un punto único de fallo; el demo no promete alta disponibilidad.

[Fuente Mermaid](arquitectura-demo.mmd)

[Persistencia, memoria y trazabilidad](persistencia.md): PostgreSQL desde el demo; ORM para entidades y adaptadores nativos para LangGraph.
