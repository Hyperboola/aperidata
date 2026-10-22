"""Passo 3 · Dai numeri alla storia.

Due tipi di storia, ognuno con i suoi grafici:
  - serie       una serie nel tempo              → colonne, linea, area
  - confronto   più categorie, ultimo periodo    → barre (classifica), ciambella (parti di un totale)

Il codice calcola tutti i numeri e li mette in un "menù", ognuno con un codice; la storia a modello sceglie dal
menù cosa mostrare e compone le frasi. Niente è inventato: ogni cifra nei testi viene dai dati. Le frasi si
rifiniscono a mano in redazione, e la redazione controlla che non compaiano numeri estranei ai dati.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import pandas as pd

from pipeline.disegna import chiusura, copertina, fmt_it, grafico, grafico_ciambella
from pipeline.periodi import Periodo

log = logging.getLogger(__name__)

INVITO = "Ti aspettavi questo numero? Giralo a chi pensa di conoscere la risposta."
HASHTAG = "#dati #statistiche #italia #istat"
LICENZA = "Dati con licenza CC BY 4.0 (creativecommons.org/licenses/by/4.0)"
TOTALI = {"IT", "ITALIA", "9", "T", "TOTAL", "TOT", "99", "ALL"}

@dataclass
class Storia:
    """Tutto ciò che serve per raccontare e disegnare: menù, elementi, scelte e testi di riserva."""
    tipo: str
    menu: dict
    elementi: dict[str, str]          # codice -> nome: ciò che si può evidenziare (periodi o categorie)
    grafici: tuple[str, ...]          # tipi di grafico ammessi
    scelte: dict                      # scelte a modello
    testi: dict                       # testi a modello
    occhiello_grafico: str
    disegna: Callable[[dict, dict, str], tuple] = field(repr=False)  # (scelte, testi, fonte) -> slide
    extra: dict = field(default_factory=dict)


# --- Formattazione ------------------------------------------------------------------------
def _var(da: float, a: float) -> float | None:
    return None if da == 0 else (a - da) / da * 100


def valore(v: float, decimali: int = 0, unita: str = "") -> str:
    return f"{fmt_it(v, decimali)}{'' if unita in ('', '%') else ' '}{unita}"


def compatto(v: float, decimali: int = 0, unita: str = "") -> str:
    """Per i numeri grandi: 1.040.000.000 -> '1,04 mld'."""
    for soglia, nome in ((1e9, "mld"), (1e6, "mln")):
        if abs(v) >= soglia:
            q = v / soglia  # 160,8 mln · 1,04 mld: due decimali solo sotto il 10, altrimenti uno
            return f"{fmt_it(q, 2 if abs(q) < 10 else 1)} {nome}{' ' + unita if unita else ''}"
    return valore(v, decimali, unita)


def variazione(p: float) -> str:
    """+63% · −4,4% · +0,3%: un decimale sotto il 10%, così le piccole variazioni non diventano 0%."""
    return f"{'+' if p > 0 else ''}{fmt_it(p, 1 if abs(p) < 10 else 0)}%"


def _maiuscola(testo: str) -> str:
    return testo[:1].upper() + testo[1:]


UNITA_CORTE = {"", "%", "€", "anni", "mesi", "giorni", "ore", "km", "kg", "t", "°C"}


def _corta(unita: str) -> str:
    """L'unità da scrivere accanto ai numeri dei grafici: solo se breve ("%", "€", "anni", "nati")."""
    return unita if unita in UNITA_CORTE or len(unita) <= 8 else ""


def _nota_unita(unita: str) -> str:
    """Le unità lunghe ("per 100 famiglie") non stanno accanto ai numeri: vanno nell'occhiello del grafico."""
    return "" if _corta(unita) == unita else f" · {unita}"


def separa_unita(numero: str, unita: str) -> tuple[str, str]:
    """'7,20 per 100 famiglie' → ('7,20', 'per 100 famiglie'): il numero grande resta grande, l'unità va sotto."""
    if unita and _corta(unita) != unita and numero.endswith(unita):
        return numero[: -len(unita)].strip(), unita
    return numero, ""


def _voce(desc: str, x: float, dec: int, unita: str) -> dict:
    return {"descrizione": desc, "testo": valore(x, dec, unita), "copertina": compatto(x, dec, unita)}


def _voce_var(desc: str, p: float) -> dict:
    return {"descrizione": desc, "testo": variazione(p), "copertina": variazione(p)}


def _cambio(da: float, a: float, tasso: bool) -> float | None:
    """Di quanto è cambiato un valore: in punti se è già una percentuale, altrimenti in %."""
    return a - da if tasso else _var(da, a)


def _voce_cambio(desc: str, da: float, a: float, dec: int, tasso: bool) -> dict | None:
    """Voce di menù per un cambiamento. Con le percentuali (tassi, quote) una 'variazione percentuale
    di una percentuale' non ha senso: si usa la differenza in punti percentuali."""
    if tasso:
        d = a - da
        return {"descrizione": desc.replace("variazione", "differenza in punti percentuali"),
                "testo": f"{'+' if d > 0 else ''}{fmt_it(d, dec)} punti", "copertina": f"{'+' if d > 0 else ''}{fmt_it(d, dec)} punti"}
    x = _var(da, a)
    return None if x is None else _voce_var(desc, x)


def _voce_diff(desc: str, d: float, dec: int, unita: str) -> dict:
    """Differenza assoluta con il segno: '+0,9 anni'. Per età, tassi e durate si capisce meglio dei %."""
    testo = f"{'+' if d > 0 else ''}{valore(d, dec, unita)}"
    return {"descrizione": desc, "testo": testo, "copertina": testo}


def e_ritmo(r: dict) -> bool:
    """Valori che sono già variazioni (ritmi di crescita), es. 'variazioni annue dei prezzi'."""
    nome = f"{r.get('fonte_testo', '')} {r.get('soggetto', '')}".lower()
    return r.get("unita") == "%" and any(p in nome for p in ("variazion", "crescita", "tendenzial", "congiuntural"))


def e_indice(r: dict, df: pd.DataFrame | None = None) -> bool:
    """Numeri indice: lo dice il nome del dataset, oppure tutti i gruppi valgono 100 nello stesso periodo."""
    nome = f"{r.get('fonte_testo', '')} {r.get('soggetto', '')}".lower()
    if "indic" in nome or "base 19" in nome or "base 20" in nome or "=100" in nome.replace(" ", ""):
        return True
    if df is not None and "categoria" in df.columns:
        per_periodo = df.dropna(subset=["obs_value"]).groupby("time_period")["obs_value"]
        return bool(((per_periodo.min() - 100).abs() < 0.05).mul((per_periodo.max() - 100).abs() < 0.05).any())
    return False


# --- Controlli sui dati ---------------------------------------------------------------------
ANNI_COVID = {2020, 2021, 2022}  # variazioni eccezionali ma vere: non fanno da metro di paragone


def salto_insolito(coppie: list[tuple[Periodo, float]], tasso: bool = False) -> str | None:
    """L'ultimo cambiamento è molto più grande di quelli abituali? Spesso non è una notizia ma un cambio di
    metodo dell'ISTAT (campo di osservazione ampliato, nuova base, nuova classificazione): va verificato."""
    cambi = [(q, _cambio(p, v, tasso)) for (_, p), (q, v) in zip(coppie, coppie[1:])]
    cambi = [(q, c) for q, c in cambi if c is not None]
    if len(cambi) < 4:
        return None
    q_ult, ultimo = cambi[-1]
    abituali = sorted(abs(c) for q, c in cambi[:-1] if q.anno not in ANNI_COVID)
    if len(abituali) < 3:
        return None
    tipico = abituali[len(abituali) // 2]
    soglia = max(3 * tipico, 1.0 if tasso else 8.0)
    if abs(ultimo) <= soglia:
        return None
    unita = " punti" if tasso else "%"
    return (f"Salto insolito {q_ult.quando}: {'+' if ultimo > 0 else ''}{fmt_it(ultimo, 1)}{unita}, contro un "
            f"cambiamento tipico di circa {fmt_it(tipico, 1)}{unita} l'anno. Prima di pubblicare verifica sul sito "
            "ISTAT se è cambiato il metodo o il campo di osservazione (serie non confrontabile).")


# --- Storia 1: una serie nel tempo ---------------------------------------------------------
def _decenni(coppie: list[tuple[Periodo, float]]) -> list[tuple[Periodo, float]]:
    """Per le proiezioni: l'ultimo dato non futuro (oggi), poi un punto ogni dieci anni fino alla fine."""
    passati = [c for c in coppie if c[0].fine() <= date.today()]
    base = passati[-1] if passati else coppie[0]
    scelti = [base] + [c for c in coppie if c[0].anno > base[0].anno and c[0].anno % 10 == 0]
    return scelti if scelti[-1] == coppie[-1] else scelti + [coppie[-1]]


def numero_indice(r: dict) -> bool:
    """Numeri indice a base fissa (es. prezzi, base 2025=100): il livello (105) non dice niente a chi legge,
    contano solo le variazioni. Più stretto di e_indice, che scambia per indici anche gli 'indicatori'."""
    nome = f"{r.get('fonte_testo', '')} {r.get('soggetto', '')}".lower()
    if r.get("unita") == "%" or "inflazione" in nome or "variazion" in nome:  # già una variazione, non un livello
        return False
    return bool(re.search(r"base\s*(19|20)\d\d|\d{4}\s*=\s*100|numeri indice|indice (armonizzato|nazionale|"
                          r"generale|dei prezzi)|prezzi al consumo|indice della produzione|indice del fatturato", nome))


ASSOLUTE = {"anni", "mesi", "giorni", "ore", "°C"}  # età e durate: '+0,7 anni' si capisce, '+2,1%' no


def storia_serie(r: dict, df: pd.DataFrame, finestra: int) -> Storia:
    coppie = [(Periodo.da_codice(p), v) for p, v in zip(df["time_period"], df["obs_value"]) if v == v]
    futuri = [q for q, _ in coppie if q.fine() > date.today()]
    proiezione = len(futuri) >= 3  # serie con stime per gli anni a venire (es. proiezioni demografiche)
    coppie = _decenni(coppie) if proiezione else coppie[-finestra:]
    if len(coppie) < 3:
        raise ValueError("Servono almeno 3 periodi con dati per raccontare una serie")
    if coppie[-1][0].freq == "M" and len(coppie) < 13 and numero_indice(r) and not r.get("_post_esistente"):
        # per un indice dei prezzi la notizia è la variazione sull'anno prima: senza, meglio un altro dataset
        # (es. la nuova base 2025 parte da gennaio 2026; la serie storica c'è in un altro dataset)
        raise ValueError("indice mensile con meno di 13 mesi: manca il confronto con l'anno prima")
    if coppie[-1][0].freq == "M" and len(coppie) < 13 and "nell'ultimo anno" in r.get("domanda", ""):
        # serie mensile che non arriva a un anno (es. una nuova base): la domanda non può promettere l'anno
        r = {**r, "domanda": r["domanda"].replace("nell'ultimo anno", "negli ultimi mesi")}
    dec, unita, sogg = r.get("decimali", 0), r.get("unita", ""), r["soggetto"]
    (a0, v0), (a1, v1), (ap, vp) = coppie[0], coppie[-1], coppie[-2]
    (amax, vmax), (amin, vmin) = max(coppie, key=lambda c: c[1]), min(coppie, key=lambda c: c[1])
    media = sum(v for _, v in coppie) / len(coppie)

    menu = {
        "ULTIMO": _voce(f"valore {a1.quando} (ultimo dato)", v1, dec, unita),
        "PRIMO": _voce(f"valore {a0.quando} (primo dato del grafico)", v0, dec, unita),
        "MASSIMO": _voce(f"valore più alto del grafico, {amax.quando}", vmax, dec, unita),
        "MINIMO": _voce(f"valore più basso del grafico, {amin.quando}", vmin, dec, unita),
        "MEDIA": _voce(f"media {a0.da} {a1.al}", media, dec, unita),
    }
    tasso = unita == "%" or unita.startswith("per 100 ")  # già percentuali: i cambiamenti si misurano in punti
    ritmo = e_ritmo(r)    # valori che sono già variazioni: niente "variazioni delle variazioni"

    def aggiungi(codice: str, desc: str, da: float, a: float) -> None:
        if not ritmo and (voce := _voce_cambio(desc, da, a, dec, tasso)) is not None:
            menu[codice] = voce

    aggiungi("VAR_PRIMO", f"variazione percentuale {a0.da} {a1.al}", v0, v1)
    if not tasso:
        menu["DIFF_PRIMO"] = _voce_diff(f"differenza assoluta {a0.da} {a1.al}", v1 - v0, dec, unita)
    aggiungi("VAR_PRECEDENTE", f"variazione {ap.da} {a1.al}", vp, v1)
    serie = {q.codice: v for q, v in coppie}
    if a1.freq != "A" and (x0 := serie.get(a1.anno_prima())) is not None:
        prima = Periodo.da_codice(a1.anno_prima())
        aggiungi("VAR_ANNO_PRIMA", f"variazione {prima.da} {a1.al} (stesso periodo, un anno dopo)", x0, v1)
    if amax != a1:
        aggiungi("VAR_DAL_MASSIMO", f"variazione {a1.quando} rispetto al massimo, {amax.quando}", vmax, v1)
    if amin != a1:
        aggiungi("VAR_DAL_MINIMO", f"variazione {a1.quando} rispetto al minimo, {amin.quando}", vmin, v1)
    for q, v in coppie:
        menu[f"PERIODO_{q.codice}"] = _voce(f"valore {q.quando}", v, dec, unita)

    # paracadute: l'angolo più forte, scelto dal codice
    # "il più alto degli ultimi N anni" e non "mai così in alto dal 2016", che fa pensare a un 2016 più alto
    arco = f"degli ultimi {len(coppie)} {({'A': 'anni', 'Q': 'trimestri', 'M': 'mesi'}).get(a1.freq, 'periodi')}"
    if (a1, v1) == (amax, vmax):
        cop, ev, titolo, did = "ULTIMO", a1, f"Il più alto {arco}", f"{sogg} {a1.quando}: il valore più alto {arco}."
    elif (a1, v1) == (amin, vmin):
        cop, ev, titolo, did = "ULTIMO", a1, f"Il più basso {arco}", f"{sogg} {a1.quando}: il valore più basso {arco}."
    elif "VAR_PRIMO" in menu and abs(_cambio(v0, v1, tasso)) >= (1 if tasso else 10):
        cop, ev, titolo = "VAR_PRIMO", a1, f"{menu['VAR_PRIMO']['testo']} {a0.da} {a1.al}"
        did = f"{sogg}: da {menu['PRIMO']['testo']} {a0.quando} a {menu['ULTIMO']['testo']} {a1.quando}."
    elif "VAR_DAL_MASSIMO" in menu and _cambio(vmax, v1, tasso) <= (-1 if tasso else -5):
        # es. il trasporto pubblico: ancora sotto il 2019. "Il picco è stato nel 2019" direbbe meno
        cop, ev, titolo = "VAR_DAL_MASSIMO", a1, f"{menu['VAR_DAL_MASSIMO']['testo']} rispetto al picco {amax.quando}"
        did = (f"{sogg} {a1.quando}: {menu['ULTIMO']['testo']}, {menu['VAR_DAL_MASSIMO']['testo']} "
               f"rispetto al picco {amax.quando} ({menu['MASSIMO']['testo']}).")
    else:
        cop, ev, titolo = "MASSIMO", amax, f"Il picco è stato {amax.quando}"
        did = (f"{sogg} {amax.quando}: da allora mai più così in alto." if amax == a0
               else f"{sogg} {amax.quando}, il valore più alto {arco}.")
    nota = f"{_maiuscola(a1.quando)}: {menu['ULTIMO']['testo']}"
    if "VAR_PRECEDENTE" in menu:
        nota += f", {menu['VAR_PRECEDENTE']['testo']} {ap.rispetto}"
    scelte = {"copertina": cop, "evidenzia": ev.codice, "grafico": "colonne",
              "chiusura": ["ULTIMO", "VAR_PRIMO"] if "VAR_PRIMO" in menu else ["ULTIMO"]}
    testi = _testi_base(r, did, titolo, nota + ".", f"{sogg} {a1.quando}.", f"{a0.rispetto}.")
    grafici = ("colonne", "linea") if ritmo else ("colonne", "linea", "area")
    if cop == "VAR_DAL_MASSIMO":  # la chiusura ripete il confronto della storia: con il picco, non con il primo anno
        scelte["chiusura"] = ["ULTIMO", "VAR_DAL_MASSIMO"]
        testi["punto_2"] = f"Rispetto al picco {amax.quando}."

    indice = numero_indice(r) and not ritmo
    if indice:
        # numeri indice: in copertina e in chiusura le variazioni, mai il livello; grafico a linea, perché
        # le colonne partono da zero e 100 contro 105 sembrano uguali
        chiave = "VAR_ANNO_PRIMA" if "VAR_ANNO_PRIMA" in menu else "VAR_PRIMO"
        rif = Periodo.da_codice(a1.anno_prima()) if chiave == "VAR_ANNO_PRIMA" else a0
        if chiave in menu:
            var = menu[chiave]["testo"]
            arco_var = "in un anno" if chiave == "VAR_ANNO_PRIMA" else f"{a0.da} {a1.al}"
            scelte.update(copertina=chiave, evidenzia=a1.codice, grafico="linea",
                          chiusura=[chiave, "VAR_PRECEDENTE"] if "VAR_PRECEDENTE" in menu else [chiave])
            testi = _testi_base(r, f"{sogg} {a1.quando}: {var} {rif.rispetto}.", f"{var} {arco_var}",
                                f"{_maiuscola(a1.quando)}: " + (f"{menu['VAR_PRECEDENTE']['testo']} {ap.rispetto}. "
                                                                if "VAR_PRECEDENTE" in menu else "")
                                + "Valori indice, non euro.", f"{rif.rispetto}.", f"{ap.rispetto}.")
        grafici = ("linea",)
    elif unita in ASSOLUTE and "DIFF_PRIMO" in menu:
        # età e durate: il cambiamento si racconta in anni, non in percentuale
        scelte["chiusura"] = ["ULTIMO", "DIFF_PRIMO"]
        menu["DIFF_PRECEDENTE"] = _voce_diff(f"differenza {ap.rispetto}", v1 - vp, dec, unita)  # numero noto
        diff = menu["DIFF_PRECEDENTE"]["testo"]
        testi["nota_grafico"] = f"{_maiuscola(a1.quando)}: {menu['ULTIMO']['testo']}, {diff} {ap.rispetto}."
        testi["post"] = f"{r['domanda']}\n\n{testi['didascalia_copertina']} {testi['nota_grafico']}"
        if cop == "VAR_PRIMO":
            scelte["copertina"] = "DIFF_PRIMO"
            testi["titolo_grafico"] = f"{menu['DIFF_PRIMO']['testo']} {a0.da} {a1.al}"

    intervallo = f"{a0.breve}–{a1.breve}"
    occhiello = f"{r['occhiello']}, {intervallo}" + (" · stime ISTAT" if proiezione else "")
    codici = [q.codice for q, _ in coppie]

    def disegna(sc: dict, tx: dict, fonte: str):
        return grafico([q.breve for q, _ in coppie], [v for _, v in coppie], forma=sc["grafico"],
                       evidenzia=codici.index(sc["evidenzia"]), decimali=dec, unita=_corta(unita), riferimento=media,
                       riferimento_testo=f"media {intervallo}: {menu['MEDIA']['testo']}", occhiello=occhiello + _nota_unita(unita),
                       titolo=tx["titolo_grafico"], nota=tx["nota_grafico"], fonte=fonte, pagina="2/3")

    extra = {"proiezione": proiezione, "periodo": a1.codice, "ultimo_valore": v1, "n_periodi": len(coppie)}
    if not proiezione and (avviso := salto_insolito(coppie, tasso)):
        extra["da_controllare"] = [avviso]
    # Serie quasi ferme (es. la popolazione: poche migliaia su 59 milioni). In percentuale verrebbe sempre "+0,0%"
    # e sulle colonne o sull'area, che partono da zero, una riga piatta: si raccontano le differenze in valore
    # assoluto, su una linea con la scala stretta (e lo si dice).
    if not tasso and not ritmo and not indice and media and (vmax - vmin) / abs(media) < 0.01:
        anno_prima = a1.anno_prima() if a1.freq != "A" else None
        rif = (Periodo.da_codice(anno_prima), serie[anno_prima]) if anno_prima in serie else (a0, v0)
        menu["DIFF_RIF"] = _voce_diff(f"differenza assoluta {rif[0].da} {a1.al}", v1 - rif[1], dec, unita)
        menu["DIFF_PRECEDENTE"] = _voce_diff(f"differenza {ap.rispetto}", v1 - vp, dec, unita)  # numero noto
        cambio, sul_prec = menu["DIFF_RIF"]["testo"], menu["DIFF_PRECEDENTE"]["testo"]
        scelte.update(copertina="DIFF_RIF", evidenzia=a1.codice, grafico="linea", chiusura=["ULTIMO", "DIFF_RIF"])
        testi = _testi_base(r, f"{_maiuscola(sogg)} {a1.quando}: {menu['ULTIMO']['testo']}, {cambio} {rif[0].rispetto}.",
                            f"{cambio} {rif[0].rispetto}",
                            f"{_maiuscola(a1.quando)}: {menu['ULTIMO']['testo']}, {sul_prec} {ap.rispetto}. "
                            "La scala non parte da zero.", f"{_maiuscola(sogg)} {a1.quando}.", f"{_maiuscola(rif[0].rispetto)}.")
        grafici = ("linea",)
    if min(v for _, v in coppie) < 0:  # colonne e area partono da zero: con valori negativi solo la linea
        grafici = ("linea",)
    return Storia("serie", menu, {q.codice: q.nome for q, _ in coppie}, grafici,
                  scelte, testi, occhiello, disegna, extra)


# --- Storia 2: confronto tra categorie nell'ultimo periodo -----------------------------------
def storia_confronto(r: dict, df: pd.DataFrame, nomi: dict[str, str]) -> Storia:
    # il periodo più recente con il maggior numero di categorie presenti
    conteggi = df.dropna(subset=["obs_value"]).groupby("time_period")["categoria"].nunique()
    periodo = Periodo.da_codice(conteggi[conteggi == conteggi.max()].index.max())
    ult = df[(df["time_period"] == periodo.codice) & df["obs_value"].notna()]
    valori = dict(zip(ult["categoria"], ult["obs_value"]))
    totale_cod = next((c for c in valori if c.upper() in TOTALI or "total" in nomi.get(c, "").lower()), None)
    parti = {c: v for c, v in valori.items() if c != totale_cod}
    if len(parti) < 2:
        raise ValueError("Servono almeno 2 categorie per un confronto")
    ordine = sorted(parti, key=parti.get, reverse=True)
    dec, unita, sogg = r.get("decimali", 0), r.get("unita", ""), r["soggetto"]
    nome = lambda c: nomi.get(c, c)  # noqa: E731
    cmax, cmin = ordine[0], ordine[-1]
    # controllo di coerenza: se c'è il totale, le parti devono "contenerlo". Un tasso nazionale sta tra il
    # minimo e il massimo delle aree; un conteggio è la loro somma. Se no manca qualcuno (es. il Mezzogiorno)
    # e la classifica direbbe il falso.
    if totale_cod:
        tot, somma = valori[totale_cod], sum(parti.values())
        margine = max(abs(tot) * 0.005, 0.05)
        dentro = parti[cmin] - margine <= tot <= parti[cmax] + margine
        sommato = tot and abs(somma / tot - 1) < 0.03
        if not (dentro or sommato):
            raise ValueError(f"classifica incompleta: il totale ({fmt_it(tot, dec)}) non è coerente con le "
                             f"{len(parti)} categorie presenti, ne manca qualcuna")
    media = sum(parti.values()) / len(parti)

    menu = {
        "MASSIMO": _voce(f"valore più alto: {nome(cmax)} ({periodo.nome})", parti[cmax], dec, unita),
        "MINIMO": _voce(f"valore più basso: {nome(cmin)} ({periodo.nome})", parti[cmin], dec, unita),
        "MEDIA": _voce(f"media delle {len(parti)} categorie ({periodo.nome})", media, dec, unita),
        "DIVARIO": _voce(f"differenza tra {nome(cmax)} e {nome(cmin)}", parti[cmax] - parti[cmin], dec, unita),
    }
    if parti[cmin] > 0:
        rapporto = parti[cmax] / parti[cmin]
        menu["RAPPORTO"] = {"descrizione": f"{nome(cmax)} rispetto a {nome(cmin)}: quante volte",
                            "testo": f"{fmt_it(rapporto, 1)} volte", "copertina": f"{fmt_it(rapporto, 1)}×"}
    if totale_cod:
        menu["TOTALE"] = _voce(f"valore complessivo: {nome(totale_cod)} ({periodo.nome})", valori[totale_cod], dec, unita)
    for c in ordine:
        menu[f"CAT_{c}"] = _voce(f"{sogg}, solo {nome(c)}, {periodo.nome}", parti[c], dec, unita)
    # le parti sommano al totale? allora si possono esprimere come quote (e disegnare a ciambella)
    somma_ok = totale_cod and valori[totale_cod] > 0 and abs(sum(parti.values()) / valori[totale_cod] - 1) < 0.03
    quote = {c: parti[c] / valori[totale_cod] * 100 for c in ordine} if somma_ok else {}
    for c, q in quote.items():
        menu[f"QUOTA_{c}"] = {"descrizione": f"quota di {nome(c)} sul totale", "testo": f"{fmt_it(q, 1)}%",
                              "copertina": f"{fmt_it(q, 1 if q < 10 else 0)}%"}
    grafici = ("barre", "ciambella") if quote and len(parti) <= 6 else ("barre",)

    scelte = {"copertina": "MASSIMO", "evidenzia": cmax, "grafico": "barre",
              "chiusura": ["MASSIMO", "MINIMO"]}
    testi = _testi_base(r, f"{_maiuscola(sogg)}, {periodo.nome}. Prima in classifica: {nome(cmax)}.",
                        f"In testa: {nome(cmax)}", f"In fondo alla classifica: {nome(cmin)}.",
                        f"{nome(cmax)}, prima in classifica.", f"{nome(cmin)}, ultima in classifica.")
    occhiello = f"{r['occhiello']}, {periodo.nome}"

    def disegna(sc: dict, tx: dict, fonte: str):
        nome_g = nome
        if sc["grafico"] == "ciambella":
            return grafico_ciambella([nome_g(c) for c in ordine], [quote[c] for c in ordine],
                                     evidenzia=ordine.index(sc["evidenzia"]), centro=menu["TOTALE"]["copertina"],
                                     occhiello=occhiello, titolo=tx["titolo_grafico"], nota=tx["nota_grafico"],
                                     fonte=fonte, pagina="2/3")
        # il totale fa da riferimento solo se è sulla stessa scala delle parti (es. un tasso medio
        # nazionale); se è la loro somma (es. abitanti) si usa la media
        if totale_cod and valori[totale_cod] <= parti[cmax] * 1.2:
            rif, rif_testo = valori[totale_cod], f"{nome_g(totale_cod)}: {menu['TOTALE']['testo']}"
        elif len(parti) >= 3:
            rif, rif_testo = media, f"media: {menu['MEDIA']['testo']}"
        else:  # con due sole barre la media non aggiunge niente
            rif, rif_testo = None, ""
        # con tante righe l'unità ripetuta su ogni barra affolla il grafico: va una volta sola, nell'occhiello
        tante = len(ordine) > 8
        return grafico([nome_g(c) for c in ordine], [parti[c] for c in ordine], forma="orizzontale",
                       evidenzia=ordine.index(sc["evidenzia"]), decimali=dec, unita="" if tante else _corta(unita),
                       riferimento=rif, riferimento_testo=rif_testo,
                       occhiello=occhiello + (f" · {unita}" if tante and unita else _nota_unita(unita)),
                       titolo=tx["titolo_grafico"],
                       nota=tx["nota_grafico"], fonte=fonte, pagina="2/3")

    return Storia("confronto", menu, {c: nome(c) for c in ordine}, grafici, scelte, testi, occhiello, disegna,
                  {"proiezione": False, "periodo": periodo.codice, "ultimo_valore": None})


def _testi_base(r: dict, didascalia: str, titolo: str, nota: str, punto_1: str, punto_2: str) -> dict:
    return {"domanda": r["domanda"], "didascalia_copertina": _maiuscola(didascalia), "titolo_grafico": titolo,
            "nota_grafico": nota, "punto_1": _maiuscola(punto_1), "punto_2": _maiuscola(punto_2),
            "invito": r.get("invito", INVITO), "post": f"{r['domanda']}\n\n{_maiuscola(didascalia)} {nota}"}


# --- Carosello -------------------------------------------------------------------------------------
# Stili delle copertine, a rotazione: (cosa c'è in copertina, sfondo della copertina, sfondo della 2ª slide).
# Con il grafico in copertina, la 2ª slide è il numero grande; con il numero in copertina, è il grafico.
STILI = {
    "numero_scuro":   ("numero", "scura", "chiara"),
    "grafico_chiaro": ("grafico", "chiara", "scura"),
    "numero_arancio": ("numero", "arancio", "chiara"),
    "numero_chiaro":  ("numero", "chiara", "scura"),
    "grafico_scuro":  ("grafico", "scura", "chiara"),
}
GRAFICI_SU_SCURO = ("linea", "area", "colonne", "barre", "orizzontale")  # colori pensati per lo sfondo scuro


def scegli_stile(precedenti: dict, ammessi: tuple, preferito: str) -> tuple[str, str]:
    """(stile, grafico): lo stile dopo l'ultimo usato; se mette il grafico in copertina, un tipo di grafico
    diverso dall'ultima copertina con grafico. Se i dati non lo permettono si passa allo stile successivo."""
    nomi = list(STILI)
    dopo = (nomi.index(precedenti["stile"]) + 1) if precedenti.get("stile") in nomi else 0
    for stile in nomi[dopo:] + nomi[:dopo]:
        cosa, sfondo_cop, sfondo_2 = STILI[stile]
        if cosa == "numero":
            return stile, preferito
        sfondo_grafico = sfondo_cop
        adatti = [g for g in ammessi if g != precedenti.get("grafico_copertina")
                  and (sfondo_grafico != "scura" or g in GRAFICI_SU_SCURO)]
        if adatti:
            return stile, preferito if preferito in adatti else adatti[0]
    return "numero_scuro", preferito


def _impagina(stile: str, numero: tuple, grafico: tuple, testi: dict) -> list:
    """Le prime due slide nello stile scelto. numero e grafico: (template, dati) già pronti."""
    cosa, sfondo_cop, sfondo_2 = STILI.get(stile, STILI["numero_scuro"])
    (t_num, d_num), (t_gr, d_gr) = numero, grafico
    if cosa == "numero":
        d_gr = {**d_gr, "tema": sfondo_2 if d_gr.get("forma") in GRAFICI_SU_SCURO else "chiara"}
        return [(t_num, {**d_num, "tema": sfondo_cop}), (t_gr, d_gr)]
    # grafico in copertina: la domanda è il suo titolo; il numero grande passa alla 2ª slide
    copertina_g = {**d_gr, "tema": sfondo_cop, "copertina": True, "titolo": testi["domanda"],
                   "nota": testi["didascalia_copertina"], "pagina": ""}
    # il titolo non ripete il numero che c'è subito sotto: "−10,0% rispetto al picco" → "Rispetto al picco"
    titolo = testi["titolo_grafico"]
    for ripetuto in (d_num.get("numero") or "", d_num.get("unita") or ""):
        if ripetuto and titolo.startswith(ripetuto):
            titolo = titolo[len(ripetuto):].strip()
    seconda = {**d_num, "tema": sfondo_2, "titolo": _maiuscola(titolo) if titolo else testi["titolo_grafico"],
               "didascalia": testi["nota_grafico"], "pagina": "2/3"}
    return [(t_gr, copertina_g), (t_num, seconda)]


def costruisci_carosello(r: dict, df: pd.DataFrame, finestra: int, nomi: dict[str, str] | None = None,
                         imposto: dict | None = None, stili_precedenti: dict | None = None) -> tuple[list, str, dict]:
    """Restituisce (slide, didascalia Instagram, rapporto).

    df: colonne time_period, obs_value e, per i confronti, categoria.
    nomi: codice di categoria -> nome leggibile.
    imposto: scelte e testi già decisi (es. corretti a mano in redazione): prevalgono su quelli a modello.
    """
    nomi = nomi or {}
    st = storia_confronto(r, df, nomi) if r.get("tipo_storia") == "confronto" else storia_serie(r, df, finestra)

    scelte, testi = dict(st.scelte), dict(st.testi)
    if r.get("grafico") in st.grafici:  # il grafico preferito (calendario o redazione), se adatto ai dati
        scelte["grafico"] = r["grafico"]
    origine = {g: "modello" for g in GRUPPI}
    rapporto = {"tipo_storia": st.tipo, "menu": st.menu, **st.extra}
    rapporto["da_controllare"] = list(st.extra.get("da_controllare", []))
    if imposto:
        scelte.update(imposto.get("scelte") or {})
        testi.update(imposto.get("testi") or {})
        origine = {g: "redazione" for g in GRUPPI}
    if (breve := r.get("breve")) and not imposto:  # nomi ISTAT lunghi: per esteso solo sotto il numero di copertina
        for campo in ("punto_1", "punto_2", "nota_grafico"):
            testi[campo] = testi[campo].replace(_maiuscola(r["soggetto"]), _maiuscola(breve)).replace(r["soggetto"], breve)
    if scelte["grafico"] not in st.grafici:  # es. colonne con valori negativi: si usa un grafico adatto ai dati
        scelte["grafico"] = st.grafici[0]
    if not scelte.get("stile") or scelte["stile"] not in STILI:  # i post già fatti tengono il loro stile
        if stili_precedenti is None:
            scelte["stile"] = "numero_scuro"
        else:
            scelte["stile"], scelte["grafico"] = scegli_stile(stili_precedenti, st.grafici, scelte["grafico"])
    rapporto.update(scelte=scelte, testi=testi, origine=origine, grafici_ammessi=list(st.grafici))

    fonte = f"Fonte: {r.get('fonte_testo', 'ISTAT')}"
    unita = r.get("unita") or ""
    punti = []
    for i, (c, campo) in enumerate(zip(scelte["chiusura"], ("punto_1", "punto_2"))):
        numero, sotto = separa_unita(st.menu[c]["copertina"], unita)
        punti.append({"numero": numero, "unita": sotto, "testo": testi[campo], "evidenzia": i == 0})
    numero, sotto = separa_unita(st.menu[scelte["copertina"]]["copertina"], unita)
    slides = [
        *_impagina(scelte["stile"],
                   copertina(occhiello=r["occhiello"], titolo=testi["domanda"], numero=numero, unita=sotto,
                             didascalia=testi["didascalia_copertina"], fonte=fonte),
                   st.disegna(scelte, testi, fonte), testi),
        chiusura(punti=punti, invito=testi["invito"], pagina="3/3"),
    ]
    testo_ig = f"{testi['post']}\n\n{fonte}. Elaborazione aperidata. {LICENZA}.\n\n{HASHTAG}"
    return slides, testo_ig, rapporto


# --- Controlli sui testi ------------------------------------------------------------------------
GRUPPI = {  # per ogni slide, i testi con la lunghezza massima in caratteri
    "copertina": {"domanda": 70, "didascalia_copertina": 130},
    "grafico": {"titolo_grafico": 60, "nota_grafico": 170},
    "chiusura": {"punto_1": 75, "punto_2": 75, "invito": 90},
    "post": {"post": 700},
}
_NUMERO = re.compile(r"\d[\d.,]*")


def pulisci(testo: str) -> str:
    """Rete di sicurezza sullo stile: niente trattini lunghi, spazi in ordine."""
    testo = re.sub(r"\s*[—–]\s*", ", ", testo)
    testo = re.sub(r",\s*([.,;:!?])", r"\1", testo)
    return re.sub(r"\s{2,}", " ", testo).strip()


def _normalizza(token: str) -> str:
    """'1.955' -> '1955', '22,35' -> '22,35', '8.' (fine frase) -> '8'."""
    token = token.rstrip(".,")
    return re.sub(r"\.(?=\d{3}(\D|$))", "", token)


def numeri(testo: str) -> set[str]:
    """I numeri scritti in un testo (gli ordinali, tipo '1° gennaio', non sono dati)."""
    testo = re.sub(r"\d+\s?°", "", testo)
    return {_normalizza(t) for t in _NUMERO.findall(testo)}
