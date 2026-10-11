# Despliegue en Dokploy con Docker Compose

Decisión del equipo: el usuario desplegará en Dokploy sobre una VPS. P06 preparará `infra/compose.yaml`, imágenes, configuración y pasos reproducibles; no se ha desplegado todavía.

## Servicios y réplicas previstas

| Servicio lógico | Inicio demo | Escalado y condición |
|---|---:|---|
| api | 1 | 2+ réplicas sin estado local; cualquier API consulta la misma DB |
| worker | 1 | 2+ consumidores con claim atómico; escalar por destinos independientes y presupuesto del modelo |
| dispatcher | 1 | 2+ solo con claim outbox concurrente probado |
| autoridad VNC / gateway | 1 autoridad demo, proceso dueño por sesión | Autoridad única por destino; los procesos gateway no se replican para el mismo escritorio |
| supervisor | 1 | Demo externo al proceso del modelo; producción particionado por dueños o coordinación probada |
| postgres | 1 | Volumen durable y backup; HA de datos es una evolución separada |
| redis | 1 | Red interna; autoridad durable permanece en PostgreSQL |
| bootstrap | Job previo único | Alembic + setup nativos; no ejecutar en cada réplica |

Los servicios lógicos pueden compartir imagen Python con comandos distintos. No montar Docker socket en workers para crear gateways: usar procesos hijos gestionados por autoridad/supervisor. Su contenedor debe permitir matar/recolectar al dueño VNC sin depender del proceso del grafo.

Capacidad inicial global: una tarea activa, aunque haya varias réplicas de API. Aumentar workers no aumenta por sí solo el límite de sesiones: configurar slots globales y locales y medirlos. Las réplicas en la misma VPS comparten CPU/RAM y punto de fallo; no equivalen a HA multihost.

## Modalidad y configuración de escalado

Dokploy distingue Docker Compose y Stack/Swarm. Para Compose registrar el comando completo de despliegue, incluyendo `--scale api=N --scale worker=M` cuando corresponda; comprobarlo en la versión instalada. El override sustituye el comando predeterminado. Stack/Swarm es alternativa multihost con imágenes preconstruidas y `deploy.replicas`; no usar `build` en ese modo. [Documentación Dokploy](https://docs.dokploy.com/docs/core/docker-compose).

No asumir que escribir `deploy.replicas` hace funcionar cualquier modalidad: verificar réplicas efectivas y balanceo en R05. La sección deploy es opcional en la especificación Compose. [Especificación Docker](https://docs.docker.com/reference/compose-file/deploy/).

No usar `container_name` fijo ni puertos de host fijos para servicios replicables. Exponer solo API por dominio HTTPS mediante el proxy de Dokploy; PostgreSQL/Redis y gateway permanecen internos. Validar que el proxy enruta a todas las réplicas; no crear un segundo proxy público innecesario.

## Persistencia y configuración

Elegimos volúmenes nombrados para PostgreSQL, Redis y evidencias privadas. En una sola VPS las autoridades usan un volumen compartido; para varios hosts, almacenamiento de objetos privado o almacenamiento compartido apropiado antes de escalar. No asumir que un volumen local existe en otro nodo.

Configurar secretos en Dokploy y mapear explícitamente las variables que necesita cada servicio. Las variables de la UI no se inyectan automáticamente a contenedores sin configuración Compose. [Configuración Dokploy](https://docs.dokploy.com/docs/core/docker-compose). Evitar `env_file` global que entregue claves de cifrado a todos los roles; no versionar `.env` real.

Mantener `LANGSMITH_TRACING=false` en demo. Fijar tags de imagen por commit/version; no desplegar latest durante evaluación. Readiness usa DB/Redis/heartbeats de instancias elegibles y bootstrap confirmado; no exige que cada instancia retirada siga viva. Healthcheck interno debe autenticar sin imprimir el token.

## Operación y gates

1. Configurar proyecto Dokploy, repositorio, ruta Compose, dominio, variables y volúmenes.
2. Construir imagen; correr migraciones y bootstrap bajo lock exclusivo; abortar release si falla.
3. Arrancar dependencias y servicios; comprobar readiness y HTTPS desde cliente externo (R01/R03).
4. Verificar R05 con 2 APIs y 2 workers: resultado durable, exclusión, duplicados, presupuesto de conexiones y número real de réplicas. Volver a capacidad inicial si no hay evidencia suficiente.
5. Antes de redeploy/reducción de workers: suspender admisión de nuevos trabajos a dueños salientes, drenar o cancelar de forma segura y confirmar cierre VNC. API puede seguir consultando resultados. Sin cierre confirmado, no reasignar destino.
6. Hacer redeploy y restart (R02/R04), comprobar volúmenes y ausencia de replay de mutaciones. Validar presupuesto PostgreSQL multiplicado por réplicas.
7. Congelar despliegues automáticos mientras corren evaluaciones; un push no debe interrumpir una sesión activa sin drenaje.

No ampliar el plazo de 300 s para completar drenaje. No añadir réplicas a supervisor/autoridad sin coordinar propiedad: un lease expirado no prueba que el proceso anterior cerró.
