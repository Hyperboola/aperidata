"""Fixture condivise: cinque serie reali (dati ISTAT pubblici) salvate in tests/fixtures/."""
import json
from pathlib import Path

import pandas as pd
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def carica():
    """carica("nascite") -> (serie, dataframe, nomi): quello che la pipeline passa a costruisci_carosello."""
    def _carica(nome: str):
        d = json.loads((FIXTURES / f"{nome}.json").read_text(encoding="utf-8"))
        return d["serie"], pd.DataFrame(d["righe"]), d["nomi"]
    return _carica
