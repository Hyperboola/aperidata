"""La memoria della pagina: cosa è già uscito.

    python -m pipeline.storico              # riepilogo e ultimi post
    python -m pipeline.storico nascite      # tutti i post di un sottotema
    python -m pipeline.storico --consumo    # quanti dataset restano nuovi, per sottotema
    python -m pipeline.storico --ricostruisci   # rimette nello storico i post che stanno in output/
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

from pipeline.calendario import carica_calendario
from pipeline.disegna import ALTRI, OUTPUT_DIR
from pipeline.run import STORICO


def carica() -> pd.DataFrame:
    voci = json.loads(STORICO.read_text()) if STORICO.exists() else []
    return pd.DataFrame(voci, columns=["data", "slot", "sottotema", "flow", "periodo", "numero", "domanda", "serie"])


def _voce(rapporto: Path) -> dict:
    """La voce di storico di un post, ricavata dai suoi file (rapporto e dati)."""
    r = json.loads(rapporto.read_text(encoding="utf-8"))
    s, sc = r["serie"], r["scelte"]
    giorno = rapporto.parent.parent  # output/AAAA/MM/GG
    dati = json.loads(rapporto.with_name(rapporto.name.replace("_rapporto", "_dati")).read_text(encoding="utf-8"))
    return {"data": f"{giorno.parent.parent.name}-{giorno.parent.name}-{giorno.name}", "serie": s["id"], "flow": s["flow"],
            "filtri": s["filtri"], "url": None, "sottotema": s["sottotema"], "slot": s["frequenza"],
            "tipo_storia": r["tipo_storia"], "grafico": sc["grafico"], "stile": sc.get("stile"),
            "periodo": str(max(x["time_period"] for x in dati["righe"])), "ultimo_valore": r.get("ultimo_valore"),
            "copertina": sc["copertina"], "numero": r["menu"][sc["copertina"]]["copertina"],
            "domanda": r["testi"]["domanda"], "titolo": r["testi"]["titolo_grafico"], "da_controllare": []}


def ricostruisci() -> int:
    """Aggiunge allo storico i post di output/ che non ci sono (utile se lo storico è stato azzerato)."""
    voci = json.loads(STORICO.read_text()) if STORICO.exists() else []
    presenti = {(v["data"], v["serie"]) for v in voci}
    nuove = []
    for rapporto in sorted(OUTPUT_DIR.glob("[0-9]" * 4 + "/[0-9][0-9]/[0-9][0-9]/" + ALTRI + "/*_rapporto.json")):
        try:
            v = _voce(rapporto)
        except Exception as exc:  # un post rovinato non deve fermare gli altri
            print(f"salto {rapporto.name}: {exc}")
            continue
        if (v["data"], v["serie"]) not in presenti:
            nuove.append(v)
            print(f"+ {v['data']} · {v['sottotema']} · {v['domanda'][:70]}")
    STORICO.write_text(json.dumps(sorted(voci + nuove, key=lambda v: v["data"]), indent=2, ensure_ascii=False),
                       encoding="utf-8")
    return len(nuove)


def main() -> None:
    parser = argparse.ArgumentParser(description="Cosa è già stato pubblicato")
    parser.add_argument("sottotema", nargs="?")
    parser.add_argument("--consumo", action="store_true", help="dataset ancora mai usati per ogni sottotema")
    parser.add_argument("--ricostruisci", action="store_true", help="rimette nello storico i post di output/")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    if args.ricostruisci:
        print(f"\n{ricostruisci()} post aggiunti allo storico.")
        return
    st = carica()
    if st.empty:
        print("Nessun post ancora pubblicato.")
        return
    with pd.option_context("display.max_colwidth", 60, "display.width", 180, "display.max_rows", 500):
        if args.consumo:
            from pipeline.categorie import candidati

            cal, usati = carica_calendario(), set(st["flow"])
            for sott, codici in cal["sottotemi"].items():
                if codici:
                    c = candidati(sott, cal)
                    nuovi = len(set(c["dataset"]) - usati)
                    print(f"{cal['giorno_di'][sott]:<10} {sott:<24} {nuovi:>4} nuovi su {len(c):>4}")
        elif args.sottotema:
            print(st[st["sottotema"] == args.sottotema][["data", "slot", "domanda", "numero", "periodo"]]
                  .to_string(index=False))
        else:
            print(f"{len(st)} post, {st['flow'].nunique()} dataset diversi, dal {st['data'].min()} al {st['data'].max()}\n")
            per = st.groupby("sottotema").agg(post=("data", "size"), ultimo=("data", "max")).sort_values("ultimo")
            print(per.to_string(), "\n")
            print(st.tail(10)[["data", "slot", "sottotema", "domanda", "numero"]].to_string(index=False))


if __name__ == "__main__":
    main()
