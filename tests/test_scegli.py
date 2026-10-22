"""Le regole con cui si sceglie la serie: i casi che in passato hanno prodotto post sbagliati."""
import itertools
from datetime import date, timedelta

import pandas as pd

import pipeline.scegli as sc
from sources.istat import slug_filtri


def codici(spec):
    """Una struttura ISTAT in miniatura: [(dimensione, codice, nome), ...]."""
    return pd.DataFrame([(d, c, n, i, d) for i, (d, c, n) in enumerate(spec)],
                        columns=["dimension", "code", "name", "position", "dimension_name"])


BASE = [("FREQ", "A", "Annuale"), ("REF_AREA", "IT", "Italia"), ("DATA_TYPE", "AR", "Arrivi")]


def test_una_voce_residuale_non_e_il_fenomeno():
    """91 mila arrivi "con regione non indicata" non sono il turismo italiano: si scarta il dataset."""
    cod = codici(BASE + [("COUNTRY", "ITNI", "Regione non indicata"), ("COUNTRY", "WORLD", "Totale paesi del mondo")])
    cols = ["freq", "ref_area", "data_type", "country", "time_period"]
    solo_residuale = pd.DataFrame([("A", "IT", "AR", "ITNI", "2025")], columns=cols)
    col_totale = pd.DataFrame([("A", "IT", "AR", "ITNI", "2025"), ("A", "IT", "AR", "WORLD", "2025")], columns=cols)
    assert sc.scegli_codici(solo_residuale, cod, "annuale", "Arrivi", {}) is None
    assert sc.scegli_codici(col_totale, cod, "annuale", "Arrivi", {})[0]["COUNTRY"] == "WORLD"


def test_l_edizione_dei_dati_non_finisce_nel_soggetto():
    """La data di pubblicazione non è una caratteristica della serie ("01-Ott-2026")."""
    cod = codici([("FREQ", "M", "Mensile"), ("REF_AREA", "IT", "Italia"), ("DATA_TYPE", "U", "Tasso di disoccupazione"),
                  ("EDITION", "2026M09G1", "01-Set-2026"), ("EDITION", "2026M10G1", "01-Ott-2026"),
                  ("AGE", "Y15-24", "15-24 anni"), ("AGE", "Y25-34", "25-34 anni")])
    cols = ["freq", "ref_area", "data_type", "edition", "age", "time_period"]
    righe = [("M", "IT", "U", e, a, "2026-08") for e in ("2026M09G1", "2026M10G1") for a in ("Y15-24", "Y25-34")]
    _, _, utili = sc.scegli_codici(pd.DataFrame(righe, columns=cols), cod, "mensile", "Disoccupazione", {})
    assert utili["parte"] == ["15-24 anni"]  # dice "15-24 anni", non l'edizione


def test_una_parte_viene_dichiarata_nel_soggetto():
    """Senza un totale, la voce scelta (es. un prodotto) va detta: "(smartphone)", non "prezzi"."""
    cod = codici([("FREQ", "M", "Mensile"), ("REF_AREA", "IT", "Italia"), ("ECOICOP", "08132", "Smartphone"),
                  ("ECOICOP", "01", "Alimentari")])
    fresche = pd.DataFrame([("M", "IT", e, "2026-09") for e in ("08132", "01")],
                           columns=["freq", "ref_area", "ecoicop", "time_period"])
    assert sc.scegli_codici(fresche, cod, "mensile", "Prezzi", {})[2]["parte"] == ["Smartphone"]


def test_il_totale_vince_sulle_parti():
    cod = codici([("FREQ", "M", "Mensile"), ("REF_AREA", "IT", "Italia"), ("ECOICOP", "00", "Indice generale"),
                  ("ECOICOP", "08132", "Smartphone")])
    fresche = pd.DataFrame([("M", "IT", e, "2026-09") for e in ("00", "08132")],
                           columns=["freq", "ref_area", "ecoicop", "time_period"])
    assert sc.scegli_codici(fresche, cod, "mensile", "Prezzi", {})[0]["ECOICOP"] == "00"


def test_una_classifica_per_aree_deve_coprire_tutta_l_italia():
    """Senza il Mezzogiorno il Centro risulterebbe "primo": una suddivisione parziale non si usa."""
    nomi = {"IT": "Italia", "ITC": "Nord-ovest", "ITD": "Nord-est", "ITE": "Centro", "ITFG": "Mezzogiorno"}
    assert sc._suddivisione_completa(["IT", "ITC", "ITD", "ITE"], nomi.get) == []
    assert sc._suddivisione_completa(["IT", "ITC", "ITD", "ITE", "ITFG"], nomi.get) == ["ITC", "ITD", "ITE", "ITFG"]


def test_note_tecniche_via_dai_nomi_istat():
    assert sc.pulisci_soggetto("Indice della retribuzione - base dicembre 2021=100") == "Indice della retribuzione"
    assert sc.pulisci_soggetto("Tasso di disoccupazione - dati mensili") == "Tasso di disoccupazione"
    assert sc.pulisci_soggetto("Popolazione inizio periodo") == "Popolazione residente"


def test_un_dataset_puo_dare_molti_post():
    """Un dataset ha molte serie (indicatore × gruppo): ognuna si pubblica una volta sola."""
    nomi = {("DATA_TYPE", "TEATRO"): "x: teatro", ("DATA_TYPE", "CINEMA"): "x: cinema",
            ("SEX", "9"): "totale", ("SEX", "1"): "maschi", ("SEX", "2"): "femmine"}
    cod = codici(BASE[:2] + [(d, c, n) for (d, c), n in nomi.items()])
    righe = [("A", "IT", dt, sx, "2025") for dt, sx in itertools.product(["TEATRO", "CINEMA"], ["9", "1", "2"])]
    fresche = pd.DataFrame(righe, columns=["freq", "ref_area", "data_type", "sex", "time_period"])
    usate, uscite = set(), []
    while scelta := sc._alternativa(fresche, cod, "annuale", "Spettacoli", {}, "F", usate):
        uscite.append(slug_filtri(scelta[0]))
        usate.add(f"F|{uscite[-1]}")
    assert len(uscite) == len(set(uscite)) == 6  # 2 indicatori × (totale, uomini, donne), nessuna ripetizione


def test_memoria_timeout_e_dataset_scartati(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "MEMORIA", tmp_path / "freschezza.json")
    assert sc._timeout({}, "LENTO") is False                       # primo timeout: si salta solo per oggi
    assert sc._noto_vecchio(sc._memoria(), "LENTO", "mensile", 6)
    sc.segna_scartato("ROTTO")                                      # es. "indice con meno di 13 mesi"
    assert sc._noto_vecchio(sc._memoria(), "ROTTO", "annuale", 24)
    dopo_20_giorni = sc._memoria()
    dopo_20_giorni["ROTTO"]["scartato"] = (date.today() - timedelta(days=20)).isoformat()
    assert not sc._noto_vecchio(dopo_20_giorni, "ROTTO", "annuale", 24)   # dopo 14 giorni si riprova
