"""Execute the public source, save all outputs exclusively in .private/."""

import os
from pathlib import Path

import nbformat
from nbclient import NotebookClient

ROOT = Path(__file__).resolve().parents[1]
os.environ["JUPYTER_PATH"] = str(ROOT / ".venv/share/jupyter") + (
    os.pathsep + os.environ["JUPYTER_PATH"] if os.environ.get("JUPYTER_PATH") else ""
)
source = ROOT / "notebooks/01-vnc-access.ipynb"
destination = ROOT / ".private/notebooks/01-vnc-access.executed.ipynb"
destination.parent.mkdir(parents=True, exist_ok=True)
notebook = nbformat.read(source, as_version=4)
client = NotebookClient(notebook, timeout=45, kernel_name="frontier-vnc",
                        resources={"metadata": {"path": str(ROOT)}})
try:
    client.execute()
except Exception as exc:
    nbformat.write(notebook, destination)
    print(f"Execution failed ({type(exc).__name__}); inspect the private notebook.")
    raise SystemExit(1) from None
nbformat.write(notebook, destination)
for cell in notebook.cells:
    for output in cell.get("outputs", []):
        if output.get("output_type") == "stream" and output.get("name") == "stdout":
            print(output["text"].strip())
print("Executed notebook saved in .private/notebooks/; public source has no outputs.")
