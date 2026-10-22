"""Il giro della giornata: calendario, rotazione dei sottotemi e delle fasce. Senza rete e senza disegnare."""
from datetime import date

import pytest

import pipeline.run as run
from pipeline.calendario import GIORNI, carica_calendario


def test_il_calendario_reale_e_valido():
    cal = carica_calendario()
    assert set(cal["slot"]) <= {"annuale", "trimestrale", "mensile"}
    assert cal["sottotemi"] and all(cal["sottotemi"].values())   # ogni sottotema ha le sue categorie ISTAT
    assert {"annuale", "mensile"} <= set(cal["freschezza"]) and {"annuale", "mensile"} <= set(cal["finestra"])
    assert cal["finestra"]["mensile"] == 13   # 13 mesi: includono lo stesso mese dell'anno prima


def test_un_sottotema_non_puo_stare_in_due_giorni(tmp_path):
    (tmp_path / "c.toml").write_text('''slot = ["annuale"]
[freschezza]
annuale = 24
[finestra]
annuale = 10
[giorni.lunedi]
tema = "A"
[giorni.lunedi.sottotemi]
prezzi = ["X"]
[giorni.martedi]
tema = "B"
[giorni.martedi.sottotemi]
prezzi = ["Y"]
''')
    with pytest.raises(ValueError, match="in due giorni"):
        carica_calendario(tmp_path / "c.toml")


def test_la_settimana_parte_dal_lunedi_dopo():
    assert run.prossimo_lunedi(date(2026, 10, 16)) == date(2026, 10, 19)   # da venerdì
    assert run.prossimo_lunedi(date(2026, 10, 17)) == date(2026, 10, 19)   # da sabato
    assert run.prossimo_lunedi(date(2026, 10, 19)) == date(2026, 10, 26)   # da lunedì: la settimana in corso è fatta


def test_tocca_al_sottotema_e_alla_fascia_usati_meno_di_recente(monkeypatch):
    cal = {"slot": ["annuale", "mensile"], "post_al_giorno": 1,
           "giorni": {"lunedi": {"tema": "Soldi", "sottotemi": {"prezzi": ["A"], "stipendi": ["B"]}}}}
    storico = [{"data": "2026-10-12", "serie": "s1", "sottotema": "prezzi", "slot": "annuale"}]
    chiamate = []

    def finto_esplora(sott, slot, cal_, storico_, creati, pubblicati, giri=4):
        chiamate.append((sott, slot))
        pubblicati.append(({}, []))
        return True

    monkeypatch.setattr(run, "pulizia", lambda: None)
    monkeypatch.setattr(run, "_storico", lambda: storico)
    monkeypatch.setattr(run, "_esplora", finto_esplora)
    monkeypatch.setattr(run, "_riepilogo", lambda *a: "riepilogo")
    run.giornata(cal, "lunedi")
    assert chiamate == [("stipendi", "mensile")]   # "prezzi" e "annuale" sono usciti per ultimi


def test_senza_tema_e_giorno_di_pausa(monkeypatch):
    monkeypatch.setattr(run, "pulizia", lambda: None)
    assert run.giornata({"giorni": {"domenica": {"tema": ""}}}, "domenica") == []
    assert run.giornata({"giorni": {}}, "sabato") == []        # un giorno non in calendario è pausa
    assert "sabato" in GIORNI


def test_i_nomi_dei_file_sono_parlanti_e_non_si_sovrascrivono(tmp_path):
    s = {"frequenza": "annuale", "sottotema": "nascite", "soggetto": "età media delle madri al parto"}
    primo = run._nome_file(s, tmp_path)
    assert primo == "annuale_nascite_eta-media-delle-madri-al-parto"
    (tmp_path / "post").mkdir()
    (tmp_path / "post" / f"{primo}_1.jpg").touch()
    assert run._nome_file(s, tmp_path) == primo + "_2"   # due post sullo stesso argomento nello stesso giorno
