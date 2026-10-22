"""Dai numeri alla storia: cosa viene scritto, e soprattutto cosa NON viene scritto."""
import pytest

from pipeline.racconta import STILI, costruisci_carosello, scegli_stile
from pipeline.redazione import numeri_sconosciuti, problemi_di_pubblicazione
from pipeline.run import stili_precedenti

SERIE = ["inflazione", "disoccupazione", "nascite", "indice_costi"]
FINESTRA = {"annuale": 10, "mensile": 13}


def costruisci(carica, nome, **kw):
    """(slide, didascalia, rapporto) come li scrive la pipeline: il rapporto porta con sé anche la serie."""
    s, df, nomi = carica(nome)
    slide, didascalia, rapporto = costruisci_carosello(s, df, FINESTRA[s["frequenza"]], nomi=nomi, **kw)
    return slide, didascalia, {"serie": s, **rapporto}


@pytest.mark.parametrize("nome", SERIE + ["inattivita_aree"])
def test_nei_testi_non_c_e_nessun_numero_inventato(carica, nome):
    """La garanzia centrale: ogni cifra scritta nei testi viene dai dati o dai calcoli sui dati."""
    slide, didascalia, rapporto = costruisci(carica, nome)
    assert len(slide) == 3
    assert numeri_sconosciuti(rapporto, rapporto["testi"]) == []


@pytest.mark.parametrize("nome", SERIE + ["inattivita_aree"])
def test_ogni_post_ha_le_parti_che_servono(carica, nome):
    _, didascalia, rapporto = costruisci(carica, nome)
    assert {"domanda", "didascalia_copertina", "titolo_grafico", "nota_grafico", "punto_1", "punto_2", "invito",
            "post"} <= set(rapporto["testi"])
    assert "Elaborazione aperidata" in didascalia and "ISTAT" in didascalia  # fonte e licenza sempre dichiarate
    assert not [p for p in problemi_di_pubblicazione(rapporto) if "Numeri" in p]


def test_una_serie_con_valori_negativi_ha_solo_la_linea(carica):
    """Colonne e area partono da zero: con un −0,2% (inflazione 2020) disegnerebbero qualcosa di falso."""
    _, _, rapporto = costruisci(carica, "inflazione")
    assert rapporto["grafici_ammessi"] == ["linea"]


def test_un_numero_indice_racconta_la_variazione_non_il_livello(carica):
    """"120" (base 2021=100) non dice niente: la copertina deve mostrare quanto è cambiato."""
    _, _, rapporto = costruisci(carica, "indice_costi")
    cover = rapporto["menu"][rapporto["scelte"]["copertina"]]["copertina"]
    assert cover.startswith("+") and cover.endswith("%")


def test_la_copertina_mostra_l_ultimo_dato_con_la_sua_unita(carica):
    _, _, rapporto = costruisci(carica, "nascite")
    assert rapporto["menu"][rapporto["scelte"]["copertina"]]["copertina"] == "32,6 anni"
    assert rapporto["menu"]["ULTIMO"]["testo"] == "32,6 anni"


def test_un_confronto_per_territorio_diventa_una_classifica(carica):
    _, _, rapporto = costruisci(carica, "inattivita_aree")
    assert rapporto["tipo_storia"] == "confronto"
    assert rapporto["scelte"]["grafico"] == "barre"


def test_una_classifica_incompleta_viene_rifiutata(carica):
    """Con il Mezzogiorno mancante, il dato Italia (50,1) sarebbe più alto di tutte le aree mostrate."""
    s, df, nomi = carica("inattivita_aree")
    senza_sud = df[df["categoria"] != "ITFG"]
    with pytest.raises(ValueError, match="incompleta"):
        costruisci_carosello(s, senza_sud, 10, nomi=nomi)


def test_i_testi_scritti_in_redazione_prevalgono_su_quelli_a_modello(carica):
    s, df, nomi = carica("nascite")
    _, _, rapporto = costruisci_carosello(s, df, 10, nomi=nomi, imposto={"testi": {"domanda": "Scritto a mano"}})
    assert rapporto["testi"]["domanda"] == "Scritto a mano"
    assert set(rapporto["origine"].values()) == {"redazione"}


def test_la_copertina_cambia_stile_a_rotazione(carica):
    primo, _ = scegli_stile({"stile": "numero_scuro"}, ("linea", "colonne"), "linea")
    secondo, _ = scegli_stile({"stile": primo}, ("linea", "colonne"), "linea")
    assert primo != secondo and primo in STILI and secondo in STILI
    # dopo l'ultimo stile si ricomincia dal primo
    assert scegli_stile({"stile": list(STILI)[-1]}, ("linea",), "linea")[0] == list(STILI)[0]


def test_due_copertine_con_grafico_non_hanno_lo_stesso_tipo_di_grafico():
    """Dopo una copertina a linea, la prossima copertina con grafico non può essere ancora una linea."""
    stile, grafico = scegli_stile({"stile": "numero_scuro", "grafico_copertina": "linea"},
                                  ("colonne", "linea", "area"), "linea")
    assert stile.startswith("grafico") and grafico != "linea"


def test_lo_storico_ricorda_l_ultimo_stile_usato():
    storico = [{"data": "2026-10-12", "stile": "numero_scuro", "grafico": "linea"},
               {"data": "2026-10-13", "stile": "grafico_chiaro", "grafico": "barre"}]
    assert stili_precedenti(storico) == {"stile": "grafico_chiaro", "grafico_copertina": "barre"}
    assert stili_precedenti([]) == {"stile": None, "grafico_copertina": None}
