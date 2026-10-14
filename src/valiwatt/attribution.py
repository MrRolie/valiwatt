"""Attribution and status lines every public surface carries. Tests check the README and reports use them verbatim."""

HQ_DATASET_URL = "https://donnees.hydroquebec.com/explore/dataset/historique-demande-electricite-quebec/"
CC_BY_NC_URL = "https://creativecommons.org/licenses/by-nc/4.0/"

HQ = {
    "fr": (
        f"Données de demande : Hydro-Québec, « Historique de la demande d'électricité au Québec » ({HQ_DATASET_URL}), "
        f"sous licence CC BY-NC 4.0 ({CC_BY_NC_URL}). Hydro-Québec offre ces données à titre informatif et sans "
        "garantie de qualité ; ce ne sont pas des données officielles. Ce projet les a contrôlées, en a masqué ou "
        "corrigé quelques heures (chaque cas est documenté) et les a agrégées ; il ne les redistribue pas."
    ),
    "en": (
        f"Demand data: Hydro-Québec, \"Historique de la demande d'électricité au Québec\" ({HQ_DATASET_URL}), "
        f"licensed CC BY-NC 4.0 ({CC_BY_NC_URL}). Hydro-Québec offers this data for information only, without "
        "warranty of quality; it is not official data. This project checked it, masked or corrected a few hours "
        "(each case documented) and aggregated it; it does not redistribute it."
    ),
}

ECCC = {
    "fr": "Données météorologiques : créé à partir de données d'Environnement et Changement climatique Canada.",
    "en": "Weather data: based on Environment and Climate Change Canada data.",
}

# The reference form ECCC's climate site asks for in its FAQ, completed with the retrieval date of each run.
ECCC_REFERENCE = {
    "fr": ("Données extraites du site Web « Données climatiques historiques » d’Environnement et Changement climatique "
           "Canada ( https://climat.meteo.gc.ca/index_f.html ) le {date}"),
    "en": ("Extracted from the Environment and Climate Change Canada Historical Climate Data web site "
           "( https://climate.weather.gc.ca/index_e.html ) on {date}"),
}

NO_ENDORSEMENT = {
    "fr": "Ni Hydro-Québec ni Environnement et Changement climatique Canada n'ont produit, revu ou approuvé ce travail.",
    "en": "Neither Hydro-Québec nor Environment and Climate Change Canada produced, reviewed or endorsed this work.",
}

AUTHORSHIP = {
    "fr": "Construit avec Claude Code sous la direction du propriétaire du dépôt.",
    "en": "Built with Claude Code under the repository owner's direction.",
}


def lines(lang: str, retrieved: str | None = None) -> list[str]:
    out = [HQ[lang], ECCC[lang]]
    if retrieved:
        out.append(ECCC_REFERENCE[lang].format(date=retrieved) + ".")
    return out + [NO_ENDORSEMENT[lang], AUTHORSHIP[lang]]
