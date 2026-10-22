from datetime import date

import pytest

from pipeline.disegna import fmt_it
from pipeline.periodi import Periodo
from pipeline.racconta import numeri, pulisci


def test_riconosce_le_tre_frequenze():
    assert Periodo.da_codice("2024").frequenza == "annuale"
    assert Periodo.da_codice("2024-Q3").frequenza == "trimestrale"
    assert Periodo.da_codice("2024-08").frequenza == "mensile"


def test_codice_sconosciuto():
    with pytest.raises(ValueError):
        Periodo.da_codice("agosto")


def test_frasi_in_italiano_corretto():
    assert Periodo.da_codice("2024-08").quando == "ad agosto 2024"   # "ad" davanti a vocale
    assert Periodo.da_codice("2024-03").quando == "a marzo 2024"
    assert Periodo.da_codice("2024").quando == "nel 2024"
    assert Periodo.da_codice("2024-Q3").nome == "3° trimestre 2024"
    assert Periodo.da_codice("2024-08").rispetto == "rispetto ad agosto 2024"
    assert Periodo.da_codice("2024").rispetto == "rispetto al 2024"


def test_eta_del_dato_in_mesi():
    assert Periodo.da_codice("2024").eta_mesi(date(2026, 1, 15)) == 13
    assert Periodo.da_codice("2025-12").eta_mesi(date(2025, 12, 31)) == 0


def test_stesso_periodo_dell_anno_prima():
    assert Periodo.da_codice("2025-08").anno_prima() == "2024-08"


def test_numeri_all_italiana():
    assert fmt_it(1950.5, 1) == "1.950,5"
    assert fmt_it(-7) == "−7"  # il segno meno vero, non il trattino


def test_numeri_scritti_in_un_testo():
    assert numeri("Nel 2025: 1.955 su 22,35 (dal 1° gennaio)") == {"2025", "1955", "22,35"}


def test_pulisci_toglie_i_trattini_lunghi():
    assert pulisci("Dati  veri — e controllati") == "Dati veri, e controllati"
