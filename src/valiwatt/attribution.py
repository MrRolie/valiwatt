"""Attribution and status lines every public surface carries. Tests check the README and the rendered reports use them
verbatim."""

HQ_DATASET_URL = "https://donnees.hydroquebec.com/explore/dataset/historique-demande-electricite-quebec/"
CC_BY_NC_URL = "https://creativecommons.org/licenses/by-nc/4.0/"

HQ = {
    "fr": (
        f"Données de demande : Hydro-Québec, « Historique de la demande d'électricité au Québec » ({HQ_DATASET_URL}), "
        f"sous licence CC BY-NC 4.0 ({CC_BY_NC_URL}). Hydro-Québec offre ces données à titre informatif et sans "
        "garantie de qualité ; ce ne sont pas des données officielles. Ce projet les a contrôlées, en a masqué ou "
        "corrigé quelques heures (chaque cas est documenté) et les a agrégées ; il ne redistribue pas le jeu de données, et "
        "les rapports n'en citent que quelques valeurs horaires, comme preuves."
    ),
    "en": (
        f"Demand data: Hydro-Québec, \"Historique de la demande d'électricité au Québec\" ({HQ_DATASET_URL}), "
        f"licensed CC BY-NC 4.0 ({CC_BY_NC_URL}). Hydro-Québec offers this data for information only, without "
        "warranty of quality; it is not official data. This project checked it, masked or corrected a few hours "
        "(each case documented) and aggregated it; it does not redistribute the dataset, and the reports quote only a few "
        "hourly values from it, as evidence."
    ),
}

# The owner ruled (2026-10-13) that the Open Government Licence – Canada governs the ECCC data (docs/data-licences.md).
ECCC = {
    "fr": ("Données météorologiques : Environnement et Changement climatique Canada, utilisées sous la Licence du "
           "gouvernement ouvert – Canada (https://ouvert.canada.ca/fr/licence-du-gouvernement-ouvert-canada)."),
    "en": ("Weather data: Environment and Climate Change Canada, used under the Open Government Licence – Canada "
           "(https://open.canada.ca/en/open-government-licence-canada)."),
}

# The reference form ECCC's climate site asks for in its FAQ, completed with the retrieval date of each run.
ECCC_REFERENCE = {
    "fr": ("Données extraites du site Web « Données climatiques historiques » d’Environnement et Changement climatique "
           "Canada ( https://climat.meteo.gc.ca/index_f.html ) le {date}"),
    "en": ("Extracted from the Environment and Climate Change Canada Historical Climate Data web site "
           "( https://climate.weather.gc.ca/index_e.html ) on {date}"),
}

# NOAA's National Blend of Models forecast, via the Iowa Environmental Mesonet: the owner approved this exact wording
# on 2026-10-16. Its home is AGENTS.md ("The NOAA and IEM attribution"); tests/test_report_render.py holds these lines
# to it, word for word.
NBM = {
    "fr": ("Prévisions météorologiques : guidance NBS du National Blend of Models de la NOAA pour la station CYUL "
           "(Montréal-Trudeau), téléchargée des archives de l'Iowa Environmental Mesonet de l'Université d'État de "
           "l'Iowa (https://mesonet.agron.iastate.edu/). Ce projet a interpolé à l'heure ces prévisions trihoraires et "
           "les a converties en degrés Celsius : les valeurs qu'il utilise sont modifiées et ne sont pas des données "
           "originales de la NOAA."),
    "en": ("Weather forecasts: NOAA National Blend of Models NBS guidance for station CYUL (Montréal-Trudeau), "
           "downloaded from the Iowa Environmental Mesonet archive of Iowa State University "
           "(https://mesonet.agron.iastate.edu/). This project interpolated these three-hourly forecasts to hourly "
           "values and converted them to degrees Celsius: the values it uses are modified and are not original NOAA "
           "data."),
}

# Widened to NOAA and the IEM, with the project affiliated with neither (the same ruling and home as NBM).
NO_ENDORSEMENT = {
    "fr": ("Ni Hydro-Québec, ni Environnement et Changement climatique Canada, ni la NOAA, ni l'Iowa Environmental "
           "Mesonet n'ont produit, revu ou approuvé ce travail, et ce projet n'est affilié ni à la NOAA ni à l'Iowa "
           "Environmental Mesonet."),
    "en": ("Neither Hydro-Québec, Environment and Climate Change Canada, NOAA nor the Iowa Environmental Mesonet "
           "produced, reviewed or endorsed this work, and the project is affiliated with neither NOAA nor the Iowa "
           "Environmental Mesonet."),
}

AUTHORSHIP = {
    "fr": "Construit avec Claude Code sous la direction du propriétaire du dépôt.",
    "en": "Built with Claude Code under the repository owner's direction.",
}


def lines(lang: str, retrieved: str | None = None) -> list[str]:
    """Every attribution line of a public surface, in order: Hydro-Québec, ECCC (with its reference when the
    retrieval date is given), NOAA's forecast via the IEM, the no-endorsement line, the authorship."""
    out = [HQ[lang], ECCC[lang]]
    if retrieved:
        out.append(ECCC_REFERENCE[lang].format(date=retrieved) + ".")
    return out + [NBM[lang], NO_ENDORSEMENT[lang], AUTHORSHIP[lang]]
