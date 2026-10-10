# 01 · Systems design — 30%

Cierre: 12:00, hora del evento. Diseño aprobado para entrega: versión 0.7. Las métricas del documento son metas; todavía no hay resultados de evaluación de las tareas completas.

## Archivos listos para cargar

- **Doc de diseño:** [systems-design.pdf](../../output/pdf/systems-design.pdf). Una página, una sola columna y arquitectura de producción al final.
- **Diagrama de arquitectura:** [arquitectura-produccion.png](../../output/arquitectura/arquitectura-produccion.png). Exportación directa desde Mermaid, con el plano de control azul; es la misma imagen incorporada al PDF.

## Fuentes y documentación

- [Diseño en Markdown](diseno.md): contenido técnico editable.
- [Fuente LaTeX](diseno.tex): versión maquetada para entrega.
- [Arquitectura](arquitectura.md): índice de las vistas de producción, demo y multiagente; cada vista incluye su fuente `.mmd`.
- [Persistencia, memoria y trazabilidad](persistencia.md): PostgreSQL, ORM, checkpoints y eventos.
- [Decisiones ampliadas](decisiones.md): escalabilidad, fallos y observabilidad.

Mantener Markdown, LaTeX y fuentes Mermaid sincronizados al iterar. Conservar una exportación vigente por vista, sin copias versionadas duplicadas.

## Vistas adicionales

- [Demo en VPS — PNG](../../output/arquitectura/arquitectura-demo.png).
- [Multiagente — PNG](../../output/arquitectura/arquitectura-multiagente.png).

## Correspondencia con la rúbrica

| Criterio | Evidencia en el diseño |
|---|---|
| Entendimiento del problema | Objetivo, supuestos, alcance, ambigüedad, cambios mínimos y plazo |
| Arquitectura y trade-offs | Flujo de ejecución, responsabilidades, alternativas y costes de cada decisión |
| Plan para medir | E1–E5, repeticiones, metas, pruebas de adaptación y fallos de infraestructura |

## Regeneración

Desde la raíz del repositorio, con Mermaid CLI disponible, exportar cada fuente usando fondo blanco y el tema predeterminado. El color del plano de control se define en la fuente de producción:

```sh
mmdc -i docs/01-systems-design/arquitectura-produccion.mmd -o output/arquitectura/arquitectura-produccion.png -b white --size 3200 -s 2
mmdc -i docs/01-systems-design/arquitectura-demo.mmd -o output/arquitectura/arquitectura-demo.png -b white --size 3200 -s 2
mmdc -i docs/01-systems-design/arquitectura-multiagente.mmd -o output/arquitectura/arquitectura-multiagente.png -b white --size 3200 -s 2
```

Con una distribución LaTeX instalada y la imagen disponible:

```sh
mkdir -p .private/pdf-build output/pdf
pdflatex -interaction=nonstopmode -halt-on-error -output-directory .private/pdf-build docs/01-systems-design/diseno.tex
pdflatex -interaction=nonstopmode -halt-on-error -output-directory .private/pdf-build docs/01-systems-design/diseno.tex
cp .private/pdf-build/diseno.pdf output/pdf/systems-design.pdf
```

Revisar visualmente el PDF después de regenerarlo y comprobar que siga siendo una página. Los archivos intermedios permanecen en `.private/`, fuera del repositorio público. No se ha enviado ningún archivo a la plataforma desde este repositorio.
