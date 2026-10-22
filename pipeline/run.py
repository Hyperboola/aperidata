"""La giornata di aperidata: calendario → dataset → serie → storia → slide.

    python -m pipeline.run                                # i post di oggi
    python -m pipeline.run --settimana                    # la prossima settimana, da lunedì a domenica
    python -m pipeline.run --settimana --settimane 2      # le prossime due settimane
    python -m pipeline.run --settimana --dal 2026-10-12   # 7 giorni da una data precisa
    python -m pipeline.run --settimana --rifai            # rifà anche i giorni già preparati
    python -m pipeline.run --giorno lunedi                # come se fosse lunedì

Ogni giorno ha un tema; i sottotemi ruotano (tocca a quello usato meno di recente) e le fasce di frequenza
(annuale, mensile) si alternano. Per ogni post: il dataset più recente non ancora usato, la serie scelta con
regole, grafico e testi di partenza a modello. I testi si rifiniscono in redazione (python -m pipeline.redazione).
Un dataset si usa una volta; una serie già uscita torna solo con un dato nuovo, dopo la pausa del calendario.

Tutto ciò che esce viene salvato in storico.json (la memoria della pagina). Risultato in output/AAAA/MM/GG/:
post/, reel/, video/ e altri/ (didascalia, rapporto, dati, riepilogo).
"""
from __future__ import annotations

import argparse
import atexit
import json
import logging
import re
import shutil
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

from config import ROOT
from pipeline.calendario import GIORNI, carica_calendario
from pipeline.disegna import ALTRI, OUTPUT_DIR, POST, cartella_giorno, render
from pipeline.periodi import Periodo
from pipeline.racconta import costruisci_carosello
from pipeline.scegli import (DatasetEsauriti, NessunaSerie, SerieNonValida, fascia_esaurita, pulisci_nome, scegli,
                             segna_scartato)
from sources.istat import Istat
from sources.rete import FonteNonDisponibile

STORICO = ROOT / "storico.json"
SCADENZE_CACHE = {"dati": 7, "serie": 35, "struttura": 120}  # giorni; i cataloghi si sovrascrivono da soli
SCADENZA_OUTPUT = 30         # giorni: oltre, le cartelle dei post vengono tolte
TENTATIVI_ESPLORAZIONE = 4   # dataset provati per ogni giro
MINUTI_PER_FASCIA = 10       # oltre, la fascia si salta: meglio un post in meno che un job infinito

log = logging.getLogger(__name__)
QUANDO = date.today()  # giorno di pubblicazione dei post che si stanno preparando (cambia con --settimana)
_istat_giu = False     # ISTAT non si raggiunge: per oggi inutile riprovare


class NonPubblicabile(Exception):
    """Oggi questa serie non va pubblicata (dato vecchio, nessun dato nuovo, serie non raccontabile)."""


# --- Storico -------------------------------------------------------------------------------------
def _storico() -> list[dict]:
    return json.loads(STORICO.read_text()) if STORICO.exists() else []


def _registra(voce: dict) -> None:
    STORICO.parent.mkdir(parents=True, exist_ok=True)
    STORICO.write_text(json.dumps(_storico() + [voce], ensure_ascii=False, indent=2))


def _ultima(chiave: str, valore: str, storico: list[dict]) -> dict | None:
    voci = [v for v in storico if v.get(chiave) == valore]
    return max(voci, key=lambda v: v["data"]) if voci else None


def stili_precedenti(storico: list[dict]) -> dict:
    """L'ultimo stile di copertina usato e il grafico dell'ultima copertina con grafico (per non ripeterli)."""
    con_stile = sorted((v for v in storico if v.get("stile")), key=lambda v: v["data"])
    col_grafico = [v for v in con_stile if v["stile"].startswith("grafico")]
    # i primi post (prima delle copertine a rotazione) erano tutti "numero su sfondo scuro"
    return {"stile": con_stile[-1]["stile"] if con_stile else ("numero_scuro" if storico else None),
            "grafico_copertina": col_grafico[-1]["grafico"] if col_grafico else None}


# --- Una serie → un carosello -------------------------------------------------------------------
def scarica(s: dict, cal: dict, forza: bool = False):
    """Dati della serie (o delle serie, per i confronti), con i controlli di base.

    Restituisce (df, nomi): df con time_period, obs_value e, se c'è una dimensione variabile, la colonna
    categoria; nomi traduce i codici di categoria in nomi leggibili.
    """
    finestra = cal["finestra"][s["frequenza"]]
    anni = {"annuale": finestra + 2, "trimestrale": finestra // 4 + 2, "mensile": 3}[s["frequenza"]]
    istat = Istat()
    df = istat.dati(s["flow"], s["filtri"], inizio=str(date.today().year - anni))
    variabile = s.get("variabile")
    nomi = {}
    if variabile:
        df = df.rename(columns={variabile.lower(): "categoria"})
        try:
            codici, _ = istat.struttura(s["flow"])
            nomi = dict(codici.loc[codici["dimension"] == variabile, ["code", "name"]].values)
        except Exception:  # senza struttura si usano i codici
            pass
        nomi = {c: pulisci_nome(n) for c, n in nomi.items()}  # niente nomi bilingui o "Provincia Autonoma"
        nomi.update(s.get("nomi") or {})
    elif df["time_period"].duplicated().any():
        raise NonPubblicabile("i filtri non individuano una sola serie")
    if s.get("decimali") is None:  # decimali dedotti dai valori
        valori = df["obs_value"].dropna().abs()
        massimo = 0 if (valori % 1 == 0).all() or valori.max() >= 100 else (1 if valori.max() >= 10 else 2)
        # mai più decimali di quelli che ISTAT pubblica: 9,6 resta 9,6 e non diventa 9,60
        osservati = max((len(f"{v:.6f}".rstrip("0").split(".")[1]) for v in valori), default=0)
        s["decimali"] = min(massimo, osservati)
    ultimo = Periodo.da_codice(df["time_period"].max())
    if ultimo.frequenza != s["frequenza"]:
        raise NonPubblicabile(f"dati {ultimo.frequenza}, serviva {s['frequenza']}")
    eta, soglia = ultimo.eta_mesi(), cal["freschezza"][s["frequenza"]]
    if eta > soglia and not forza:
        raise NonPubblicabile(f"ultimo dato {ultimo.nome}, {eta} mesi fa (massimo {soglia})")
    return df, nomi


def esegui(s: dict, cal: dict, storico: list[dict] | None = None) -> tuple[list[Path], dict]:
    """Scarica la serie, costruisce il carosello e scrive i file. Restituisce (file creati, voce di storico)."""
    df, nomi = scarica(s, cal)
    if storico is not None and (ultima := _ultima("serie", s["id"], storico)):
        if df["time_period"].max() <= ultima["periodo"]:  # stessi dati di prima: il post sarebbe uguale
            raise NonPubblicabile("nessun dato nuovo dall'ultima volta")

    slides, testo_ig, rapporto = costruisci_carosello(s, df, cal["finestra"][s["frequenza"]], nomi=nomi,
                                                      stili_precedenti=stili_precedenti(storico or []))
    out = cartella_giorno(QUANDO)
    altri = out / ALTRI
    altri.mkdir(parents=True, exist_ok=True)
    nome = _nome_file(s, out)
    paths = render(slides, nome=nome, out_dir=out)
    (altri / f"{nome}_didascalia.txt").write_text(testo_ig, encoding="utf-8")
    # i dati esatti del post: la redazione rigenera da questi, senza chiedere niente a ISTAT
    (altri / f"{nome}_dati.json").write_text(json.dumps({"righe": df.to_dict("records"), "nomi": nomi},
                                                      ensure_ascii=False, default=str), encoding="utf-8")
    (altri / f"{nome}_rapporto.json").write_text(json.dumps({"serie": s, **rapporto}, ensure_ascii=False, indent=2),
                                                encoding="utf-8")
    scelte = rapporto["scelte"]
    log.info("%s: storia %s, grafico %s, copertina %s", s["id"], rapporto["tipo_storia"], scelte["grafico"],
             scelte["copertina"])
    try:
        url = Istat().url_dati(s["flow"], s["filtri"])  # il puntatore: da qui si riscaricano i dati
    except Exception:
        url = None
    voce = {"data": QUANDO.isoformat(), "serie": s["id"], "flow": s["flow"], "filtri": s["filtri"],
            "url": url, "sottotema": s["sottotema"], "slot": s["frequenza"], "tipo_storia": rapporto["tipo_storia"],
            "grafico": scelte["grafico"], "stile": scelte.get("stile"), "periodo": df["time_period"].max(),
            "ultimo_valore": rapporto.get("ultimo_valore"), "copertina": scelte["copertina"],
            "numero": rapporto["menu"][scelte["copertina"]]["copertina"],
            "domanda": rapporto["testi"]["domanda"], "titolo": rapporto["testi"]["titolo_grafico"],
            "da_controllare": rapporto.get("da_controllare") or []}
    if voce["da_controllare"]:
        log.warning("⚠ %s da controllare prima di pubblicare: %s", nome, "; ".join(voce["da_controllare"]))
    return paths + [altri / f"{nome}_didascalia.txt", altri / f"{nome}_rapporto.json"], voce


def _nome_file(s: dict, cartella: Path) -> str:
    """Nome parlante: fascia_sottotema_argomento, es. annuale_nascite_eta-media-delle-madri."""
    argomento = unicodedata.normalize("NFKD", s["soggetto"]).encode("ascii", "ignore").decode().lower()
    parole, argomento = re.sub(r"[^a-z0-9]+", " ", argomento).split(), ""
    for parola in parole:  # taglia a parola intera, entro 40 caratteri
        if len(argomento) + len(parola) + 1 > 40:
            break
        argomento = f"{argomento}-{parola}" if argomento else parola
    nome = f"{s['frequenza']}_{s['sottotema']}_{argomento}"
    n = 2
    while (cartella / POST / f"{nome}_1.jpg").exists():  # due post sullo stesso argomento nello stesso giorno
        nome = f"{s['frequenza']}_{s['sottotema']}_{argomento}_{n}"
        n += 1
    return nome


# --- La giornata -----------------------------------------------------------------------------------
def pulizia() -> None:
    """All'avvio: via la cache scaduta e i caroselli vecchi. Lo storico non si tocca mai."""
    tolti = Istat().pulisci(SCADENZE_CACHE)
    for cartella in OUTPUT_DIR.glob("[0-9]" * 4 + "/[0-9][0-9]/[0-9][0-9]"):  # output/AAAA/MM/GG
        giorno = date(int(cartella.parent.parent.name), int(cartella.parent.name), int(cartella.name))
        if (date.today() - giorno).days > SCADENZA_OUTPUT:
            shutil.rmtree(cartella)
            tolti += 1
    for vuota in sorted(OUTPUT_DIR.glob("[0-9]" * 4 + "/*"), reverse=True) + sorted(OUTPUT_DIR.glob("[0-9]" * 4)):
        if vuota.is_dir() and not any(vuota.iterdir()):  # mesi e anni rimasti vuoti
            vuota.rmdir()
    if tolti:
        log.info("Pulizia: tolti %s file o cartelle scaduti", tolti)


def giornata(cal: dict, giorno: str) -> list[Path]:
    pulizia()
    g = cal["giorni"].get(giorno, {})
    if not g.get("tema"):
        log.info("%s: giorno di pausa", giorno)
        return []
    storico = _storico()
    uso = {}  # per ogni serie, sottotema e fascia: l'ultima data in cui è uscita
    for v in storico:
        for chiave in (v["serie"], v["sottotema"], f"fascia:{v['slot']}"):
            uso[chiave] = max(uso.get(chiave, ""), v["data"])
    # Le fasce si alternano: prima quella pubblicata meno di recente
    fasce = sorted(cal["slot"], key=lambda f: (uso.get(f"fascia:{f}", ""), cal["slot"].index(f)))
    quanti = cal.get("post_al_giorno", len(fasce))
    log.info("%s · %s · %s post, fasce in ordine: %s", giorno, g["tema"], quanti, ", ".join(fasce))

    sottotemi = [s for s, cod in g.get("sottotemi", {}).items() if cod]
    ordine = sorted(sottotemi, key=lambda s: (uso.get(s, ""), sottotemi.index(s)))
    log.info("Sottotema del giorno: %s", ordine[0] if ordine else "-")
    creati, pubblicati, usate = [], [], set()
    # Prima il sottotema a cui tocca, in ogni fascia; solo se non ha niente di pubblicabile si passa al
    # successivo. Ogni post: sottotema e fascia diversi.
    for sott in ordine:
        if len(pubblicati) >= quanti:
            break
        rimaste = [f for f in fasce if f not in usate]
        for slot in rimaste:
            # la fascia preferita ha pochi tentativi: se lì non c'è, si passa subito alla successiva, che riusa
            # quello che si è appena letto. L'ultima fascia ha tutti i giri.
            giri = TENTATIVI_ESPLORAZIONE if slot == rimaste[-1] else 2
            if _esplora(sott, slot, cal, storico, creati, pubblicati, giri=giri):
                usate.add(slot)
                break
        else:
            log.info("%s: niente di pubblicabile in nessuna fascia, provo il sottotema successivo", sott)
    if len(pubblicati) < quanti:
        log.warning("%s: pubblicati %s post su %s", g["tema"], len(pubblicati), quanti)
    if pubblicati:
        creati.append(_riepilogo(giorno, g["tema"], pubblicati))
    return creati


def _pubblica(s: dict, cal: dict, storico: list[dict], creati: list, pubblicati: list) -> bool:
    try:
        paths, voce = esegui(s, cal, storico=storico)
    except NonPubblicabile as exc:
        log.info("%s/%s: salto %s, %s", s["sottotema"], s["frequenza"], s["id"], exc)
        segna_scartato(s["flow"])
        return False
    except Exception as exc:  # fonte giù, filtri sbagliati...: si prova la prossima
        log.warning("%s/%s: %s non riuscita: %s", s["sottotema"], s["frequenza"], s["id"], exc)
        if not isinstance(exc, FonteNonDisponibile):  # se è ISTAT a essere giù, il dataset non c'entra
            segna_scartato(s["flow"])  # altrimenti lo si riscaricherebbe e fallirebbe uguale ad ogni lancio
        return False
    _registra(voce)
    storico.append(voce)
    creati += paths
    pubblicati.append((voce, paths))
    return True


def _esplora(sott: str, slot: str, cal: dict, storico: list[dict], creati: list, pubblicati: list,
             giri: int = TENTATIVI_ESPLORAZIONE) -> bool:
    """Prima i dataset mai usati; poi le serie mai pubblicate dei dataset già usati; per ultime le serie già
    pubblicate, fuori dalla pausa e solo se c'è un dato nuovo."""
    global _istat_giu
    if _istat_giu:
        return False
    try:  # se la memoria dice già che qui non c'è niente di questa fascia, inutile cercare
        if n := fascia_esaurita(sott, slot, cal):
            log.info("%s/%s: nessuno dei %s dataset già controllati ha dati %s freschi, salto subito la fascia",
                     sott, slot, n, slot)
            return False
    except Exception as exc:  # catalogo non disponibile: si cerca come sempre
        log.debug("controllo fascia non riuscito: %s", exc)
    pausa = cal["pausa_settimane"] * 7
    mai = {v["flow"] for v in storico}
    recenti = {v["flow"] for v in storico if abs((QUANDO - date.fromisoformat(v["data"])).days) < pausa}
    falliti, inizio = set(), time.monotonic()  # dataset che oggi non vanno: non si riprovano
    usate = {v["serie"] for v in storico}
    giri_possibili = ((mai, usate, "dataset mai usati"),
                      (set(), usate, "altra serie di un dataset già usato"),  # es. teatro dopo i musei
                      (recenti, set(), "serie già usata, fuori pausa"))
    for esclusi, serie_usate, giro in giri_possibili:
        provati = esclusi | falliti
        for _ in range(giri):
            if time.monotonic() - inizio > MINUTI_PER_FASCIA * 60:
                log.warning("%s/%s: superati %s minuti, salto la fascia", sott, slot, MINUTI_PER_FASCIA)
                return False
            try:
                s = scegli(sott, slot, cal, esclusi=provati, serie_usate=serie_usate)
            except DatasetEsauriti:
                break  # tutti usati: si passa al giro successivo
            except SerieNonValida as exc:  # quel dataset non va: se ne prova un altro
                log.info("%s/%s: %s", sott, slot, exc)
                provati |= exc.flows
                falliti |= exc.flows
                continue
            except NessunaSerie as exc:
                log.info("%s/%s: %s", sott, slot, exc)
                continue
            except FonteNonDisponibile as exc:  # ISTAT giù o irraggiungibile: stop per oggi, senza martellare
                _istat_giu = True
                log.warning("ISTAT non raggiungibile (%s): per oggi mi fermo. Riprova tra qualche ora; "
                            "se il sito si apre nel browser, prova con un'altra rete.", str(exc)[:160])
                return False
            except Exception as exc:
                log.warning("%s/%s: esplorazione non riuscita: %s", sott, slot, exc)
                return False
            log.info("%s/%s: %s", sott, slot, giro)
            provati.add(s["flow"])
            if _pubblica(s, cal, storico, creati, pubblicati):
                return True
            falliti.add(s["flow"])
        else:
            return False  # tentativi finiti senza aver esaurito i dataset nuovi: niente giro successivo
    return False


def _riepilogo(giorno: str, tema: str, pubblicati: list) -> Path:
    """output/AAAA/MM/GG/altri/riepilogo.md: i post del giorno con didascalie, pronti da pubblicare."""
    righe = [f"# {QUANDO.isoformat()} · {giorno} · {tema}\n"]
    for voce, paths in pubblicati:
        slide = ", ".join(p.name for p in paths if p.suffix == ".jpg" and "_reel_" not in p.name)
        reel = ", ".join(p.name for p in paths if p.suffix == ".jpg" and "_reel_" in p.name)
        didascalia = next(p for p in paths if p.name.endswith("_didascalia.txt")).read_text(encoding="utf-8")
        avvisi = [f"> ⚠ **Da controllare:** {a}" for a in voce.get("da_controllare") or []]
        righe += [f"## {voce['slot']} · {voce['sottotema']}", *avvisi, f"**{voce['domanda']}** ({voce['numero']})",
                  f"Slide: {slide}", f"Reel: {reel}",
                  f"Video: {', '.join(p.name for p in paths if p.suffix == '.mp4') or '-'}", "", "```", didascalia, "```", ""]
    path = cartella_giorno(QUANDO) / ALTRI / "riepilogo.md"
    path.write_text("\n".join(righe), encoding="utf-8")
    return path


# --- Da riga di comando -----------------------------------------------------------------------------
def prossimo_lunedi(oggi: date | None = None) -> date:
    """Il lunedì della prossima settimana: da sabato o domenica è dopodomani o domani,
    da lunedì è il lunedì dopo (la settimana in corso si considera già preparata)."""
    oggi = oggi or date.today()
    return oggi + timedelta(days=7 - oggi.weekday())


def gia_preparato(giorno: date) -> bool:
    """Ci sono già post per quel giorno di pubblicazione?"""
    return any((cartella_giorno(giorno) / ALTRI).glob("*_rapporto.json"))


def _durata(secondi: float) -> str:
    """3725 -> '1 h 2 min 5 s'."""
    s = int(secondi)
    parti = [(s // 3600, "h"), (s % 3600 // 60, "min"), (s % 60, "s")]
    return " ".join(f"{n} {u}" for n, u in parti if n) or "0 s"


def _timer() -> None:
    """Segna inizio e fine della pipeline; la fine si scrive anche se la interrompi con Ctrl+C."""
    avvio, t0 = datetime.now(), time.monotonic()
    log.info("▶ Inizio pipeline: %s", avvio.strftime("%d/%m/%Y %H:%M:%S"))
    atexit.register(lambda: log.info("■ Fine pipeline: %s · durata %s", datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                                     _durata(time.monotonic() - t0)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera i caroselli di aperidata")
    parser.add_argument("--giorno", choices=GIORNI, help="esegui come se fosse questo giorno")
    parser.add_argument("--settimana", action="store_true", help="prepara i post della prossima settimana, da lunedì "
                        "a domenica (o 7 giorni da --dal), ognuno nella cartella del suo giorno di pubblicazione")
    parser.add_argument("--settimane", type=int, default=1, help="con --settimana: quante settimane preparare")
    parser.add_argument("--dal", help="con --settimana: primo giorno, AAAA-MM-GG")
    parser.add_argument("--rifai", action="store_true", help="con --settimana: rifà anche i giorni già preparati "
                        "(di solito si saltano, per non avere post doppi)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    _timer()

    global QUANDO
    cal = carica_calendario()
    if args.settimana:
        inizio = date.fromisoformat(args.dal) if args.dal else prossimo_lunedi()
        creati = []
        log.info("ISTAT accetta poche richieste al minuto: la prima volta ci vuole tempo (anche decine di minuti "
                 "a giorno). Puoi interrompere con Ctrl+C e rilanciare: i giorni già pronti si saltano e ciò che "
                 "si è scoperto sui dataset resta in memoria, quindi i giri successivi sono molto più veloci.")
        for i in range(7 * max(1, args.settimane)):
            QUANDO = inizio + timedelta(days=i)
            if not args.rifai and gia_preparato(QUANDO):
                log.info("──── %s ──── già preparato, salto (usa --rifai per rifarlo)", QUANDO.isoformat())
                continue
            log.info("──── %s ────", QUANDO.isoformat())
            t_giorno = time.monotonic()
            creati += giornata(cal, GIORNI[QUANDO.weekday()])
            log.info("⏱ %s fatto in %s", QUANDO.isoformat(), _durata(time.monotonic() - t_giorno))
    else:
        creati = giornata(cal, args.giorno or GIORNI[date.today().weekday()])
    for path in creati:
        print(f"Creato {path}")


if __name__ == "__main__":
    main()
