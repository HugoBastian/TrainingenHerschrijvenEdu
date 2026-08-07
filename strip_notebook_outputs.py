"""Haalt uitvoer uit `herschrijven.ipynb` voor het naar git gaat.

Celuitvoer bevat vaak echte trainingsdata (id's, titels, batchstatussen) — precies wat
`.gitignore` voor `*.xlsx` en `herschreven/` probeert te weren. Notebookuitvoer glipt daar
doorheen omdat die in het `.ipynb`-bestand zelf zit. Dit script is de git clean-filter die dat
afvangt (zie `.gitattributes`); het kan ook los op een pad worden aangeroepen.

Eenmalige lokale installatie (per checkout, git-filters staan niet in de repo zelf):
    git config filter.strip-notebook-outputs.clean "python3 strip_notebook_outputs.py"
    git config filter.strip-notebook-outputs.smudge cat
    git config filter.strip-notebook-outputs.required true
"""

from __future__ import annotations

import json
import sys


def strip(nb: dict) -> dict:
    for cell in nb.get("cells", []):
        if cell.get("cell_type") == "code":
            cell["execution_count"] = None
            cell["outputs"] = []
    return nb


def main() -> None:
    if len(sys.argv) > 1:
        pad = sys.argv[1]
        with open(pad, encoding="utf-8") as f:
            nb = json.load(f)
        nb = strip(nb)
        with open(pad, "w", encoding="utf-8") as f:
            json.dump(nb, f, indent=1, ensure_ascii=False)
            f.write("\n")
    else:
        nb = json.load(sys.stdin)
        nb = strip(nb)
        json.dump(nb, sys.stdout, indent=1, ensure_ascii=False)
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
