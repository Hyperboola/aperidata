"""Lettura e controllo di calendario.toml."""
from __future__ import annotations

import tomllib
from pathlib import Path

from config import ROOT

FREQUENZE = ("annuale", "trimestrale", "mensile")
GIORNI = ["lunedi", "martedi", "mercoledi", "giovedi", "venerdi", "sabato", "domenica"]


def carica_calendario(path: Path = ROOT / "calendario.toml") -> dict:
    with open(path, "rb") as fh:
        cal = tomllib.load(fh)
    if fasce := [s for s in cal["slot"] if s not in FREQUENZE]:
        raise ValueError(f"calendario.toml: fasce sconosciute {fasce} (ammesse: {FREQUENZE})")
    cal["sottotemi"], cal["giorno_di"] = {}, {}
    for giorno in GIORNI:
        for sott, codici in cal["giorni"].get(giorno, {}).get("sottotemi", {}).items():
            if sott in cal["sottotemi"]:
                raise ValueError(f"calendario.toml: sottotema {sott!r} in due giorni")
            cal["sottotemi"][sott], cal["giorno_di"][sott] = codici, giorno
    racconto = cal.setdefault("racconto", {})
    if sconosciuti := set(racconto) - set(cal["sottotemi"]) - {"default"}:
        raise ValueError(f"calendario.toml, [racconto]: sottotemi sconosciuti {sorted(sconosciuti)}")
    return cal


def preferenze(cal: dict, sottotema: str) -> dict:
    """Come raccontare un sottotema: la sua voce in [racconto], completata da quella di default."""
    return {**cal.get("racconto", {}).get("default", {}), **cal.get("racconto", {}).get(sottotema, {})}
