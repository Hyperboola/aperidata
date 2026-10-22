"""Disegno vero, con Chromium e ffmpeg (circa tre minuti in tutto):  python -m pytest -m slow"""
import struct
import subprocess
import sys
from datetime import date
from pathlib import Path

import imageio_ffmpeg
import pytest
from PIL import Image

import pipeline.disegna as disegna
import pipeline.run as run
from pipeline.calendario import carica_calendario
from pipeline.racconta import costruisci_carosello

pytestmark = pytest.mark.slow
ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "nascite.json"


def _slide(carica):
    s, df, nomi = carica("nascite")
    return costruisci_carosello(s, df, 10, nomi=nomi)[0]


def _scatole(mp4: Path) -> list[str]:
    """I blocchi di primo livello di un MP4: un file sano ha un solo 'moov'."""
    dati, i, scatole = mp4.read_bytes(), 0, []
    while i + 8 <= len(dati):
        dimensione, tipo = struct.unpack(">I4s", dati[i:i + 8])
        scatole.append(tipo.decode("latin1"))
        if dimensione < 8:
            break
        i += dimensione
    return scatole


def _errori_di_decodifica(mp4: Path) -> list[str]:
    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(mp4), "-f", "null", "-"],
                       capture_output=True, text=True)
    return [riga for riga in r.stderr.splitlines() if riga.strip()]


def test_slide_e_reel_hanno_le_misure_giuste(carica, tmp_path):
    paths = disegna.render(_slide(carica), nome="t", out_dir=tmp_path, video=False)
    misure = {p.parent.name: Image.open(p).size for p in paths}
    assert misure == {"post": (1080, 1350), "reel": (1080, 1920)}   # 4:5 per il feed, 9:16 per i reel
    assert len(paths) == 6                                           # tre slide per formato


def test_il_video_e_integro(carica, tmp_path):
    mp4 = next(p for p in disegna.render(_slide(carica), nome="t", out_dir=tmp_path) if p.suffix == ".mp4")
    assert _scatole(mp4).count("moov") == 1
    assert _errori_di_decodifica(mp4) == []


def test_due_rigenerazioni_insieme_non_rovinano_il_video(tmp_path):
    """Regressione: due "Salva e rigenera" ravvicinati scrivevano lo stesso file e il video usciva fermo
    (due blocchi 'moov', migliaia di errori di decodifica). Ora si scrive in un file provvisorio."""
    codice = ("import json, sys; from pathlib import Path; import pandas as pd\n"
              "from pipeline.racconta import costruisci_carosello; from pipeline.disegna import render\n"
              "d = json.load(open(sys.argv[1]))\n"
              "slide = costruisci_carosello(d['serie'], pd.DataFrame(d['righe']), 10, nomi=d['nomi'])[0]\n"
              "render(slide, nome='t', out_dir=Path(sys.argv[2]))\n")
    processi = [subprocess.Popen([sys.executable, "-c", codice, str(FIXTURE), str(tmp_path)], cwd=ROOT,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(2)]
    assert [p.wait() for p in processi] == [0, 0]
    mp4 = tmp_path / "video" / "t_video.mp4"
    assert _scatole(mp4).count("moov") == 1
    assert _errori_di_decodifica(mp4) == []
    assert not list((tmp_path / "video").glob("*.tmp.mp4"))          # nessun file provvisorio rimasto


def test_esegui_scrive_i_file_nelle_cartelle_giuste(carica, tmp_path, monkeypatch):
    """Il giro completo di un post, con ISTAT sostituito dai dati salvati."""
    s, df, nomi = carica("nascite")
    cal = carica_calendario()

    class IstatFinto:
        def url_dati(self, flow, filtri):
            return "https://esempio/dati"

    monkeypatch.setattr(disegna, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(run, "scarica", lambda s_, cal_, forza=False: (df, nomi))
    monkeypatch.setattr(run, "Istat", IstatFinto)
    monkeypatch.setattr(run, "QUANDO", date(2026, 10, 19))
    paths, voce = run.esegui(dict(s), cal, storico=[])

    giorno = tmp_path / "2026" / "10" / "19"
    assert sorted(p.name for p in giorno.iterdir()) == ["altri", "post", "reel", "video"]
    assert {p.name.rsplit("_", 1)[-1] for p in (giorno / "altri").iterdir()} >= {"rapporto.json", "dati.json", "didascalia.txt"}
    assert voce["data"] == "2026-10-19" and voce["numero"] == "32,6 anni" and voce["serie"] == s["id"]
    # lo stesso dato non si ripubblica: nessun dato nuovo dall'ultima volta
    with pytest.raises(run.NonPubblicabile, match="nessun dato nuovo"):
        run.esegui(dict(s), cal, storico=[voce])
