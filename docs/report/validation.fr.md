# Validation et surveillance d'une prévision de la demande d'électricité au Québec

_Rapport généré à partir d'une chaîne d'exécutions MLflow suivie depuis le rejeu de 2024 (section 11), code `9ab28e51a1ec`, empreinte du code `6894ad412fe0`, règles de surveillance gelées au commit `affe089f6ed0` (étiquette `rules-frozen`), **après une dérogation aux règles gelées d'abord : le rejeu de 2024 est a posteriori (section 7)**. Chaque valeur mesurée vient de ces exécutions, et chaque constante de conception (embargo, rééchantillonnages, quantiles, horizons, facteurs, années) des exécutions ou du code qui les a produites ; aucune n'est saisie à la main. Restent écrits dans le texte : les numéros de section, de règle et des normes citées, les unités du calendrier (24 heures, semaines du lundi au dimanche), le niveau de 95 % des intervalles de confiance, les formules, les décomptes d'un raisonnement (un jour et son écho) et la description des bris de la section 9. La version anglaise est dérivée de celle-ci._

> Données de demande : Hydro-Québec, « Historique de la demande d'électricité au Québec » (https://donnees.hydroquebec.com/explore/dataset/historique-demande-electricite-quebec/), sous licence CC BY-NC 4.0 (https://creativecommons.org/licenses/by-nc/4.0/). Hydro-Québec offre ces données à titre informatif et sans garantie de qualité ; ce ne sont pas des données officielles. Ce projet les a contrôlées, en a masqué ou corrigé quelques heures (chaque cas est documenté) et les a agrégées ; il ne redistribue pas le jeu de données, et les rapports n'en citent que quelques valeurs horaires, comme preuves.
>
> Données météorologiques : Environnement et Changement climatique Canada, utilisées sous la Licence du gouvernement ouvert – Canada (https://ouvert.canada.ca/fr/licence-du-gouvernement-ouvert-canada).
>
> Données extraites du site Web « Données climatiques historiques » d’Environnement et Changement climatique Canada ( https://climat.meteo.gc.ca/index_f.html ) le 2026-10-14.
>
> Ni Hydro-Québec ni Environnement et Changement climatique Canada n'ont produit, revu ou approuvé ce travail.
>
> Construit avec Claude Code sous la direction du propriétaire du dépôt.

## En bref

- Sur 2020–2023, la prévision s'écarte en moyenne de 4,42 % de la demande horaire, contre 7,81 % pour une prévision naïve (la même heure, une semaine avant) : elle fait mieux, et l'écart est confirmé.
- Ses intervalles à 90 % ont contenu la demande 90,6 % du temps depuis 2021-01, et l'IC 95 % de cette couverture contient le nominal. Ils en contiennent moins que promis dans ces groupes : l'hiver ; les déciles de charge prévue 7, 8, 9 et 10 (section 4).
- Aux 5 heures les plus chargées de 2019–2023, toutes en 2023-02, la demande est sortie de l'intervalle 5 fois sur 5, toujours par le haut.
- Rejouée comme en production, avec le modèle et les règles gelés, l'année 2024 n'a déclenché aucune recommandation (EAMP 4,23 %, couverture 89,4 %), **a posteriori**, après une dérogation aux règles gelées.
- Le rapport mesure aussi ce que le moniteur pouvait manquer : sur 2023, une hausse soutenue de 10 % de la demande a donné « réentraîner » en 10 semaines à 0 des 4 départs, et sur 2024 (hors échantillon, mesure ajoutée après les résultats), à 1 des 4 ; sur 2024, un écart soutenu de ±2 % entre la demande et la prévision, à 1 des 8 cas (sections 6 à 8).

## Fiche du modèle

| | |
|---|---|
| **Ce qu'il prévoit** | La demande horaire d'électricité du Québec publiée par Hydro-Québec, pour les 24 heures du jour J, émise à la fin du jour J−1 (heure normale de l'Est), avec un intervalle de prévision à 90 %. |
| **Usage prévu** | Démontrer, sur une prévision réelle, une validation et une surveillance du type de celles d'une équipe de gestion du risque de modèle. |
| **Hors champ** | Toute répartition opérationnelle de la production, tout négoce ou tarification, toute décision. Hydro-Québec publie ces données à titre informatif ; ce ne sont pas des données officielles. |
| **Données** | Demande horaire de Hydro-Québec ; température horaire d'une station principale, Montréal (ECCC), la seule que le modèle utilise, et de 5 autres (Québec, Gatineau, Sherbrooke, Saguenay et Trois-Rivières) pour une analyse de sensibilité seulement. Conception sur 2019–2023 ; 2024 rejouée comme en production. |
| **Modèle** | Moindres carrés ordinaires sur des variables nommées : une constante par heure et type de jour, le chauffage et la climatisation (degrés-heures, base 18 °C) par bloc horaire, la demande de la veille et de la semaine précédente. Figé sur les données jusqu'au 2023-12-24 ; empreinte de ses coefficients et de sa demi-largeur `c8e1725aad77` (modèle enregistré `valiwatt-demand-persistence`, alias `replay-2024`). |
| **Intervalle** | Prévision × (1 ± 9,26 %). La demi-largeur est un quantile des 8 739 erreurs relatives hors échantillon de 2023-01 à 2023-12, faites par les 12 modèles réajustés chaque mois de la validation et déjà notées quand le modèle figé est ajusté (section 8), puis appliqué à ce modèle : la forme d'un intervalle conforme « split », sans sa garantie de couverture en échantillon fini (section 1). La couverture mesurée en est la preuve (sections 4 et 7). |
| **Erreur en validation** | EAMP de 4,42 % [4,14 % ; 4,68 %] sur 2020–2023, contre 7,81 % pour la référence naïve. |
| **Rejeu de 2024** | EAMP de 4,23 %, couverture de 89,4 % ; aucune recommandation (section 7) ; **a posteriori** : rejoué à nouveau après une dérogation aux règles gelées, décidée après que les résultats de 2024 eurent été vus et consignée dans `AGENTS.md` (section « Departures from the frozen monitoring rules », qui en donne la liste) ; le rejeu d'avant est montré à côté (section 7). Sur 2024, un écart soutenu de ±6 % ou plus entre la demande et la prévision a donné « réentraîner » en 8 semaines au plus à chacun des 4 départs ; un écart de +2 %, à 0 des 4 départs, et de −2 %, à 1 des 4. Sur 2024, des intervalles réduits à × 0,5 de leur demi-largeur ont donné « recalibrer » à 3 des 4 départs ; des réductions moindres (jusqu'à × 0,9), à 1 des 4 départs au plus. |
| **Cas tests** | Mars à juin 2020 : biais de −1,86 % contre −0,60 % les mêmes mois des autres années ; écart non confirmé par des intervalles disjoints ; l'intervalle de la différence, ajouté après les résultats, confirme l'écart (section 3). Pointe de froid de 2023-02 : 0 des 5 heures les plus chargées dans l'intervalle. Aucun des deux cas n'avait de critère d'acceptation qui pouvait échouer. |
| **Mise en garde météo** | La production utilise la température de la veille, seule connue à l'émission. La température observée de l'heure visée, qui tient lieu d'une température de Montréal parfaitement prévue, n'est qu'une borne optimiste ; elle n'émet aucune recommandation. |
| **Limites** | Section 8. |
| **Auteur** | Construit avec Claude Code sous la direction du propriétaire du dépôt. |

## 1. Les méthodes, en mots simples

- **Validation progressive.** Chaque mois de 2020 à 2023 est prévu par un modèle réajusté sur les heures qui le précèdent seulement, moins les 7 derniers jours (l'embargo). Aucune information du mois testé ne sert à l'ajuster.
- **L'erreur.** L'erreur absolue moyenne en pourcentage (EAMP) : en moyenne, de combien la prévision s'écarte de la demande réelle. Le biais signé dit dans quel sens : biais = (demande − prévision) / demande, positif quand la prévision est trop basse.
- **L'incertitude d'une moyenne.** Les heures voisines partagent la météo ; on rééchantillonne donc des semaines entières, 1 000 fois, pour obtenir un intervalle à 95 % autour de chaque moyenne. « A bat B » n'est écrit que si l'intervalle de la différence exclut zéro.
- **L'intervalle de prévision.** Sa demi-largeur est un quantile des erreurs relatives |demande − prévision| / prévision. Pour chaque mois de validation, c'est le quantile de rang ⌈(n + 1) × 0,90⌉ des n erreurs hors échantillon des 12 mois précédents déjà notées quand le modèle du mois est ajusté (section 8), chacun de ces mois prévu par son propre modèle réajusté ; pour le modèle figé, le même quantile sur les erreurs des 12 mois qui précèdent l'année rejouée, déjà notées quand il est ajusté, appliqué à un modèle qui n'a produit aucune de ces erreurs. C'est la forme d'un intervalle conforme « split », mais sa garantie de couverture en échantillon fini ne tient pas ici : elle suppose des erreurs échangeables, et ces erreurs horaires sont autocorrélées et viennent d'autres modèles. La seule preuve est la couverture mesurée hors échantillon (sections 4, 6 et 7).
- **Le modèle figé.** En production, le modèle n'est pas réajusté : il est ajusté une fois, figé avec l'empreinte de ses coefficients, puis rejoué heure par heure.
- **Les seuils.** Pour chaque saison, les seuils hebdomadaires sont des quantiles des semaines de validation : EAMP au-dessus du 95ᵉ centile, biais hors de l'intervalle 2,5ᵉ–97,5ᵉ centile, couverture sous le 5ᵉ centile.
- **La dérive.** Chaque semaine, la distribution des températures et celle des prévisions sont comparées aux mêmes semaines (± 2) des années précédentes, avec la distance de Wasserstein normée d'Evidently. Le niveau d'alarme est le 95ᵉ centile de cette distance sur les semaines de validation.
- **La table de décision.** Chaque semaine, au plus une recommandation, avec ses preuves : (1) un échec de qualité des données → examiner les données ; (2) la même défaillance 2 semaines de suite (EAMP au-dessus du seuil, ou biais du même côté de sa bande), étendue à toute la semaine, ou le biais du même côté de sa bande 3 semaines de suite → réentraîner ; (3) couverture sous le seuil 2 semaines de suite, étendue, biais dans sa bande → recalibrer les intervalles ; (4) la même entrée en dérive 2 semaines de suite, performance dans ses seuils → examiner les données. « Étendue » : la semaine reste hors seuil quand on écarte ses 2 jours les plus extrêmes. Un seul mauvais jour ne touche jamais plus de deux jours d'une semaine (lui-même et, par les retards de 24 h et de 168 h, le lendemain ou le même jour la semaine suivante) : il ne peut donc déclencher une recommandation que si les semaines contenaient déjà des jours en échec. Une mauvaise semaine du lundi au dimanche revient la semaine suivante, par le retard d'une semaine, avec le signe opposé : ce n'est pas la même défaillance deux fois et, si les semaines voisines restent dans leurs bandes, elle ne déclenche rien non plus. Un épisode de 3 jours ou plus peut, lui, déclencher « réentraîner » à lui seul ; la section 6 mesure à quelle fréquence. Le moniteur recommande ; il n'agit jamais.

## 2. Validation sur 2020–2023

48 plis mensuels, de 2020-01 à 2023-12, embargo de 7 jours.

| Prévision | EAMP | IC 95 % | Biais signé | Heures |
|---|---|---|---|---|
| météo de la veille (production) | 4,42 % | [4,14 % ; 4,68 %] | +0,22 % | 34 997 |
| météo observée (borne optimiste) | 3,40 % | [3,29 % ; 3,51 %] | +1,22 % | 34 997 |
| même heure, une semaine avant | 7,81 % | [7,14 % ; 8,44 %] | −0,60 % | 35 046 |
| météo de la veille, moyenne des 6 stations | 4,45 % | [4,17 % ; 4,73 %] | −0,01 % | 35 013 |

Les différences d'EAMP sont en points de pourcentage (pt).

- Météo de la veille contre la référence naïve : −3,40 pt d'EAMP [−3,92 pt ; −2,91 pt], écart confirmé.
- Météo observée contre météo de la veille : −1,01 pt [−1,27 pt ; −0,73 pt] ; c'est au plus ce qu'apporterait, dans ce modèle, une température de Montréal parfaitement prévue.
- Moyenne des 6 stations contre la station principale (analyse de sensibilité, jamais utilisée pour choisir ; le plan a fixé la station de Montréal après que le rapport de qualité des données eut comparé les deux sources sur 2019–2023, des années qui couvrent celles de la validation) : +0,04 pt [+0,01 pt ; +0,07 pt].

![EAMP mensuelle](figures/validation/validation-monthly-mape.fr.png)

**Par saison et par année** (météo de la veille) :

| Groupe | EAMP | IC 95 % | Biais | IC 95 % du biais |
|---|---|---|---|---|
| automne | 3,83 % | [3,40 % ; 4,27 %] | +0,38 % | [−0,09 % ; +0,90 %] |
| printemps | 5,15 % | [4,69 % ; 5,62 %] | −1,42 % | [−1,97 % ; −0,86 %] |
| été | 2,61 % | [2,42 % ; 2,79 %] | +0,83 % | [+0,50 % ; +1,10 %] |
| hiver | 6,11 % | [5,61 % ; 6,61 %] | +1,12 % | [+0,40 % ; +1,72 %] |
| 2020 | 4,72 % | [4,14 % ; 5,29 %] | −0,31 % | [−0,99 % ; +0,38 %] |
| 2021 | 4,30 % | [3,81 % ; 4,91 %] | +0,45 % | [−0,05 % ; +0,95 %] |
| 2022 | 4,45 % | [3,92 % ; 5,02 %] | +0,49 % | [−0,06 % ; +1,04 %] |
| 2023 | 4,19 % | [3,68 % ; 4,75 %] | +0,27 % | [−0,31 % ; +0,84 %] |

**Par décile de charge prévue** (les déciles sont pris sur la prévision, jamais sur la demande réelle) :

| Décile | EAMP | IC 95 % | Biais | IC 95 % du biais |
|---|---|---|---|---|
| 1 | 2,59 % | [2,40 % ; 2,81 %] | +1,09 % | [+0,77 % ; +1,41 %] |
| 2 | 3,29 % | [3,04 % ; 3,56 %] | 0,00 % | [−0,35 % ; +0,37 %] |
| 3 | 3,12 % | [2,88 % ; 3,41 %] | +0,53 % | [+0,19 % ; +0,87 %] |
| 4 | 3,16 % | [2,91 % ; 3,44 %] | +0,33 % | [−0,03 % ; +0,65 %] |
| 5 | 3,55 % | [3,25 % ; 3,93 %] | −0,23 % | [−0,58 % ; +0,15 %] |
| 6 | 4,91 % | [4,51 % ; 5,34 %] | −0,64 % | [−1,25 % ; −0,02 %] |
| 7 | 5,93 % | [5,53 % ; 6,35 %] | +0,77 % | [+0,03 % ; +1,58 %] |
| 8 | 5,90 % | [5,49 % ; 6,40 %] | +0,60 % | [−0,16 % ; +1,41 %] |
| 9 | 5,75 % | [5,29 % ; 6,21 %] | +0,40 % | [−0,29 % ; +1,10 %] |
| 10 | 5,98 % | [5,37 % ; 6,55 %] | −0,60 % | [−1,50 % ; +0,31 %] |

**Par heure de la journée** (heure locale où commence l'heure de demande) :

| Heure | EAMP | IC 95 % | Biais | IC 95 % du biais |
|---|---|---|---|---|
| 0 h | 4,58 % | [4,24 % ; 4,91 %] | +0,29 % | [−0,08 % ; +0,65 %] |
| 1 h | 4,74 % | [4,39 % ; 5,09 %] | +0,24 % | [−0,14 % ; +0,62 %] |
| 2 h | 4,90 % | [4,54 % ; 5,26 %] | +0,19 % | [−0,22 % ; +0,58 %] |
| 3 h | 5,02 % | [4,64 % ; 5,40 %] | +0,13 % | [−0,28 % ; +0,53 %] |
| 4 h | 5,10 % | [4,71 % ; 5,48 %] | +0,04 % | [−0,36 % ; +0,44 %] |
| 5 h | 5,12 % | [4,73 % ; 5,49 %] | −0,09 % | [−0,51 % ; +0,31 %] |
| 6 h | 5,17 % | [4,81 % ; 5,52 %] | −0,04 % | [−0,45 % ; +0,33 %] |
| 7 h | 4,95 % | [4,63 % ; 5,27 %] | +0,03 % | [−0,33 % ; +0,38 %] |
| 8 h | 4,38 % | [4,08 % ; 4,68 %] | +0,21 % | [−0,11 % ; +0,52 %] |
| 9 h | 4,04 % | [3,77 % ; 4,33 %] | +0,33 % | [+0,04 % ; +0,60 %] |
| 10 h | 3,93 % | [3,66 % ; 4,20 %] | +0,25 % | [−0,03 % ; +0,51 %] |
| 11 h | 4,00 % | [3,73 % ; 4,25 %] | +0,30 % | [+0,03 % ; +0,55 %] |
| 12 h | 4,09 % | [3,80 % ; 4,35 %] | +0,36 % | [+0,09 % ; +0,60 %] |
| 13 h | 4,25 % | [3,96 % ; 4,51 %] | +0,36 % | [+0,07 % ; +0,62 %] |
| 14 h | 4,34 % | [4,06 % ; 4,60 %] | +0,33 % | [+0,04 % ; +0,59 %] |
| 15 h | 4,36 % | [4,07 % ; 4,62 %] | +0,27 % | [−0,03 % ; +0,56 %] |
| 16 h | 4,31 % | [4,03 % ; 4,57 %] | +0,33 % | [+0,03 % ; +0,61 %] |
| 17 h | 4,13 % | [3,87 % ; 4,38 %] | +0,25 % | [−0,04 % ; +0,51 %] |
| 18 h | 3,99 % | [3,74 % ; 4,24 %] | +0,28 % | [0,00 % ; +0,54 %] |
| 19 h | 3,99 % | [3,74 % ; 4,24 %] | +0,29 % | [+0,01 % ; +0,56 %] |
| 20 h | 4,05 % | [3,79 % ; 4,30 %] | +0,31 % | [+0,01 % ; +0,58 %] |
| 21 h | 4,06 % | [3,78 % ; 4,33 %] | +0,19 % | [−0,12 % ; +0,47 %] |
| 22 h | 4,16 % | [3,87 % ; 4,44 %] | +0,25 % | [−0,06 % ; +0,55 %] |
| 23 h | 4,36 % | [4,06 % ; 4,67 %] | +0,29 % | [−0,04 % ; +0,61 %] |

**Coefficients du modèle figé pour 2024.** Chaque coefficient dit ce qu'une variable ajoute *compte tenu* de la demande de la veille et de la semaine précédente ; l'ajustement sans retards, montré à côté pour l'interprétation seulement, donne l'effet total de la météo. Les 72 constantes (une par heure et type de jour) ne sont pas montrées. Aucune erreur type des moindres carrés n'est donnée : les erreurs sont autocorrélées.

| Variable | Modèle figé | Sans retards |
|---|---|---|
| chauffage, nuit (0–5 h) (MW par degré-heure)  | 109,9 | 489,3 |
| climatisation, nuit (0–5 h) (MW par degré-heure)  | 109,6 | 517,2 |
| chauffage, matin (6–9 h) (MW par degré-heure)  | 117,1 | 516,3 |
| climatisation, matin (6–9 h) (MW par degré-heure)  | 64,5 | 315,4 |
| chauffage, jour (10–15 h) (MW par degré-heure)  | 103,5 | 439,4 |
| climatisation, jour (10–15 h) (MW par degré-heure)  | 49,0 | 192,6 |
| chauffage, soir (16–20 h) (MW par degré-heure)  | 114,5 | 494,1 |
| climatisation, soir (16–20 h) (MW par degré-heure)  | 52,6 | 210,0 |
| chauffage, fin de soirée (21–23 h) (MW par degré-heure)  | 106,3 | 462,1 |
| climatisation, fin de soirée (21–23 h) (MW par degré-heure)  | 91,6 | 404,9 |
| demande de la veille (part)  | 0,599 | — |
| demande de la semaine précédente (part)  | 0,169 | — |

## 3. Cas tests

Aucun des deux cas tests n'avait de critère d'acceptation qui pouvait échouer : le plan fixait ce que chacun mesurerait, pas un résultat qui compterait comme un échec. Ce sont des mesures.

**Printemps 2020.** Mois de mars, avril, mai et juin : biais de −1,86 % [−3,06 % ; −0,71 %] en 2020, contre −0,60 % [−1,12 % ; −0,11 %] les autres années. Le critère retenu, des intervalles disjoints, ne confirme pas l'écart ; ce n'est pas la règle de la section 1, qui juge l'intervalle de la différence. *Ajouté après les résultats :* l'intervalle de la différence, calculé au rendu avec le même rééchantillonnage par semaines entières que les exécutions, mais avec plus de tirages (chaque côté sur ses propres semaines et avec sa propre graine, car ils ne partagent aucune heure), donne −1,27 pt [−2,51 pt ; −0,02 pt] : selon la règle de la section 1, l'écart est confirmé (400 000 tirages par côté, en 20 lots indépendants de 20 000 ; erreur type de Monte-Carlo des bornes, mesurée par leur dispersion d'un lot à l'autre : 0,0026 pt et 0,0032 pt ; bornes à cette précision : −2,5092 pt et −0,0188 pt, chacune à plus de 3 de ses erreurs types de zéro). Avec la météo observée, le même cas donne −1,39 % [−2,30 % ; −0,44 %] en 2020 contre +1,28 % [+0,78 % ; +1,75 %] : intervalles disjoints ; différence −2,66 pt [−3,71 pt ; −1,59 pt], ajoutée après les résultats par le même calcul : selon la règle de la section 1, l'écart est confirmé (400 000 tirages par côté, en 20 lots indépendants de 20 000 ; erreur type de Monte-Carlo des bornes, mesurée par leur dispersion d'un lot à l'autre : 0,0020 pt et 0,0025 pt ; bornes à cette précision : −3,7121 pt et −1,5938 pt, chacune à plus de 3 de ses erreurs types de zéro). Les intervalles de prévision ne couvrent pas 2020 : ils demandent 12 mois de calibration.

| Mois | EAMP | Biais |
|---|---|---|
| 2020-03 | 5,41 % | −1,50 % |
| 2020-04 | 5,52 % | −3,03 % |
| 2020-05 | 4,72 % | −2,48 % |
| 2020-06 | 3,04 % | −0,43 % |
| 2021-03 | 7,76 % | −0,96 % |
| 2021-04 | 6,85 % | −1,54 % |
| 2021-05 | 3,48 % | −0,73 % |
| 2021-06 | 3,06 % | +1,55 % |
| 2022-03 | 5,40 % | −0,75 % |
| 2022-04 | 4,86 % | −1,41 % |
| 2022-05 | 3,01 % | −0,50 % |
| 2022-06 | 2,22 % | +1,24 % |
| 2023-03 | 4,31 % | −0,94 % |
| 2023-04 | 6,42 % | −2,20 % |
| 2023-05 | 4,10 % | −1,07 % |
| 2023-06 | 2,28 % | +0,15 % |

**Pointe de froid de 2023-02.** Les 5 heures les plus chargées de 2019–2023 tombent toutes en 2023-02. 0 sur 5 sont dans l'intervalle à 90 %. Même avec la température observée de l'heure visée, la prévision reste de 10,8 % à 12,7 % sous la demande : le retard de la température n'explique pas tout l'écart. La cause n'est pas testée : aucun ajustement sans les retards, ni avec une autre réponse à la température, n'a été fait sur cette pointe. Ce que montrent les coefficients du modèle figé pour 2023, ajusté jusqu'au 2022-12-24, avant la pointe (les plis de validation qui l'ont prévue sont des réajustements de même forme, dont les coefficients ne sont pas conservés) : la prévision reprend la demande de la veille avec un poids de 0,586, et un degré-heure de chauffage de plus lui ajoute 107 à 119 MW selon le bloc horaire, contre 439 à 514 MW dans l'ajustement sans retards. Une prévision ainsi adossée à la veille ne suit, le jour même, qu'une partie d'un écart de température ; c'est une lecture des coefficients, pas une cause mesurée. Une seule pointe est étudiée ici.

![Pointe de froid](figures/validation/validation-peak-hours.fr.png)

| Heure finissant à (UTC) | Demande | Température | Météo de la veille | Intervalle | Météo observée |
|---|---|---|---|---|---|
| 2023-02-03 23:00 UTC | 42 473 MW | −26,1 °C | 31 016 MW | 28 051–33 981 | 37 062 MW |
| 2023-02-04 00:00 UTC | 42 254 MW | −26,2 °C | 30 964 MW | 28 004–33 924 | 37 033 MW |
| 2023-02-04 01:00 UTC | 41 799 MW | −26,3 °C | 30 435 MW | 27 526–33 344 | 36 591 MW |
| 2023-02-04 02:00 UTC | 41 141 MW | −26,4 °C | 29 650 MW | 26 816–32 484 | 35 940 MW |
| 2023-02-03 22:00 UTC | 40 585 MW | −26,1 °C | 29 910 MW | 27 051–32 769 | 36 190 MW |

## 4. Intervalles de prévision

Nominal 90 %, à partir de 2021-01.

| Forme | Couverture | IC 95 % | Demi-largeur moyenne |
|---|---|---|---|
| relative, un quantile (production) | 90,6 % | [88,7 % ; 92,5 %] | 9,99 % |
| relative, un quantile par saison (sensibilité) | 90,1 % | [88,6 % ; 91,7 %] | 9,65 % |
| absolue (sensibilité) | 90,1 % | [87,9 % ; 92,4 %] | 11,93 % |
| relative, météo observée | 90,2 % | [89,0 % ; 91,4 %] | 7,60 % |

L'IC 95 % de la couverture globale contient le nominal. La couverture conditionnelle se lit sur les IC 95 % des tableaux ci-dessous, tels qu'imprimés, pour l'intervalle de production. Sous-couverts, leur IC 95 % entièrement sous le nominal de 90 % : l'hiver ; les déciles de charge prévue 7, 8, 9 et 10. Sur-couverts, l'IC entièrement au-dessus : l'été et l'automne ; les déciles de charge prévue 1, 2, 3, 4 et 5. Un quantile par saison est une analyse de sensibilité. Avec un quantile par saison, l'IC 95 % de la couverture contient le nominal de 90 % dans chaque saison. Les IC des deux formes ne se chevauchent pas en hiver et en été ; ils se chevauchent au printemps et en automne. Aucun intervalle n'est calculé sur l'écart entre les deux formes : la comparaison s'arrête au chevauchement des IC. Le tableau des déciles montre ce que le quantile par saison laisse aux fortes charges. La forme de production reste celle fixée avant la validation ; en changer après avoir vu les résultats rendrait ceux-ci non indépendants du choix.

![Couverture par saison](figures/validation/validation-coverage-by-season.fr.png)

| Saison | Un quantile | IC 95 % | Un quantile par saison | IC 95 % |
|---|---|---|---|---|
| hiver | 80,0 % | [75,8 % ; 83,9 %] | 89,2 % | [86,0 % ; 92,0 %] |
| printemps | 87,9 % | [84,4 % ; 91,6 %] | 88,9 % | [85,8 % ; 92,3 %] |
| été | 99,4 % | [99,0 % ; 99,8 %] | 90,9 % | [88,3 % ; 93,4 %] |
| automne | 94,8 % | [92,2 % ; 97,1 %] | 91,5 % | [88,0 % ; 94,3 %] |

| Bloc horaire | Couverture (un quantile) | IC 95 % |
|---|---|---|
| nuit (0–5 h) | 87,5 % | [84,8 % ; 90,2 %] |
| matin (6–9 h) | 89,5 % | [87,3 % ; 91,7 %] |
| jour (10–15 h) | 92,0 % | [90,2 % ; 93,8 %] |
| soir (16–20 h) | 92,8 % | [91,1 % ; 94,4 %] |
| fin de soirée (21–23 h) | 91,6 % | [89,7 % ; 93,6 %] |

| Décile de charge prévue | Couverture (un quantile) | IC 95 % | Un quantile par saison | IC 95 % |
|---|---|---|---|---|
| 1 | 98,5 % | [97,4 % ; 99,5 %] | 95,1 % | [93,0 % ; 96,8 %] |
| 2 | 96,7 % | [95,0 % ; 98,1 %] | 92,5 % | [90,3 % ; 94,5 %] |
| 3 | 96,7 % | [95,1 % ; 98,0 %] | 92,3 % | [90,1 % ; 94,3 %] |
| 4 | 97,2 % | [95,9 % ; 98,4 %] | 91,6 % | [89,1 % ; 93,8 %] |
| 5 | 96,0 % | [94,0 % ; 97,7 %] | 90,9 % | [88,4 % ; 93,4 %] |
| 6 | 90,9 % | [87,7 % ; 93,8 %] | 87,6 % | [83,9 % ; 90,7 %] |
| 7 | 82,1 % | [77,6 % ; 85,8 %] | 84,0 % | [79,5 % ; 87,6 %] |
| 8 | 81,4 % | [77,4 % ; 85,0 %] | 85,7 % | [82,1 % ; 89,2 %] |
| 9 | 84,2 % | [79,8 % ; 87,7 %] | 90,5 % | [87,1 % ; 93,6 %] |
| 10 | 82,5 % | [78,1 % ; 87,4 %] | 90,7 % | [87,7 % ; 93,7 %] |

## 5. Seuils de surveillance

Tirés des semaines de validation 2021–2023 (le rejeu de 2023 n'utilise que 2021–2022). Saisons : hiver de décembre à février, printemps de mars à mai, été de juin à août, automne de septembre à novembre. Semaines de lundi à dimanche, heure normale. Une semaine n'est jugée par le rejeu, et n'entre dans les seuils, que si ses 7 jours sont tous dans la période (l'année rejouée, ou les années dont les seuils sont tirés), qu'aucun n'échoue au contrôle de qualité des données du jour du rejeu et qu'elle compte au moins 120 heures notées : les deux appliquent le même test. Pour ces raisons, 4 semaines de 2021–2023 ont été écartées (4 de 2021–2022 pour le rejeu de 2023) ; l'exécution de validation consigne chacune avec sa raison.

| Saison | EAMP au-dessus de | Biais hors de | Couverture sous | Semaines |
|---|---|---|---|---|
| hiver | 8,35 % | −3,10 % à +5,47 % | 61,2 % | 36 |
| printemps | 8,33 % | −5,20 % à +2,13 % | 64,9 % | 38 |
| été | 3,75 % | −0,49 % à +2,60 % | 96,4 % | 40 |
| automne | 6,00 % | −1,45 % à +4,10 % | 82,0 % | 39 |

Dérive : alarme au-dessus de 1,252 pour la température et de 1,015 pour la prévision.

**Seuils du rejeu de 2023**, tirés des semaines 2021–2022 ; les mesures de la section 6 sur 2023 les utilisent, celles sur 2024 les seuils du tableau précédent :

| Saison | EAMP au-dessus de | Biais hors de | Couverture sous | Semaines |
|---|---|---|---|---|
| hiver | 8,19 % | −2,33 % à +5,22 % | 58,0 % | 24 |
| printemps | 8,38 % | −3,83 % à +2,60 % | 62,5 % | 25 |
| été | 3,95 % | −0,49 % à +2,74 % | 95,5 % | 26 |
| automne | 6,02 % | −1,76 % à +4,81 % | 77,4 % | 26 |

Chaque seuil est un quantile extrême des semaines d'une saison (colonne « Semaines ») : avec si peu de semaines, il tient à quelques semaines extrêmes et il bouge quand on ajoute une année, comme le montrent les deux tableaux. La fréquence réelle des semaines signalées à tort est donc incertaine.

## 6. Le modèle figé, répété sur 2023

Avant de rejouer 2024, la même procédure est répétée sur les années de conception : modèle figé sur les données jusqu'au 2022-12-24, seuils tirés de 2021–2022, 2023 rejouée heure par heure. Les mesures de cette section portent d'abord sur 2023, l'année sur laquelle les règles de surveillance ont été conçues et revues avant le gel : la règle 2 a été réécrite avant le gel, et sa première formulation dit « réentraîner » sur cette année (plus bas) ; sur 2023, leurs taux de fausses alertes et d'angles morts ne sont donc pas mesurés sur une année que les règles n'avaient pas vue. Les fausses alertes des épisodes courts, le changement de niveau et le coût de l'exclusion de l'écho sont aussi mesurés sur 2024, hors échantillon, et montrés à côté de 2023 : cette mesure sur 2024 a été ajoutée après les résultats de 2024. La puissance des règles sur 2024 est en section 7.

| | Modèle figé | Plis réajustés chaque mois |
|---|---|---|
| EAMP | 4,18 % | 4,19 % |
| Biais | +0,34 % | +0,27 % |
| Couverture | 92,3 % | 90,4 % |
| Semaines EAMP hors seuil | 1 | 1 |
| Semaines biais au-dessus de sa bande | 1 | 1 |
| Semaines biais au-dessous de sa bande | 3 | 3 |
| Semaines couverture basse | 0 | 0 |
| Recommandations | 0 | 0 |

Différence d'EAMP (figé moins réajusté), sur les mêmes 8 739 heures : −0,01 pt [−0,02 pt ; 0,00 pt] : aucun écart confirmé ; figer le modèle un an ne coûte pas de précision mesurable sur 2023.

**Pourquoi « la même défaillance, étendue ».** La première formulation de la règle 2 (EAMP ou biais hors seuil 2 semaines de suite, dans n'importe quelle combinaison) dit « réentraîner » la semaine du 2023-02-06 (la semaine d'avant : EAMP 8,69 % hors seuil, biais +4,93 % ; celle-ci : EAMP 7,95 %, biais −5,47 % hors bande), puis la semaine du 2023-02-27 (la semaine d'avant : EAMP 7,99 %, biais +5,93 % hors bande ; celle-ci : EAMP 5,58 %, biais −3,93 % hors bande). Chaque fois, le biais change de signe d'une semaine à l'autre : c'est le profil d'un écart passager que les retards de 24 h et de 168 h reportent sur les jours suivants ; un modèle dégradé garderait plutôt une erreur de même signe. Semaine du 2023-02-06 : les deux semaines ne montrent pas la même défaillance, et la règle retenue n'y voit rien. Semaine du 2023-02-27 : les deux semaines ne montrent pas la même défaillance, et la règle retenue n'y voit rien. La règle retenue exige la même défaillance 2 semaines de suite, étendue à chaque semaine : un jour isolé ne suffit pas si les semaines ne contenaient pas déjà des jours en échec ; un épisode de plusieurs jours peut suffire, et la fréquence en est mesurée plus bas.

**Puissance des règles sur 2023.** À partir de la première semaine jugée de janvier, avril, juillet et octobre, on impose un changement soutenu et on rejoue les règles hebdomadaires sur les heures de 2023 : un écart entre la demande et la prévision (de ±2 % à ±8 %), compté en semaines avant « réentraîner », ou des intervalles rétrécis (demi-largeur × 0,9 à × 0,5), comptés en semaines avant « recalibrer » (— : rien en 8 semaines). Une mesure, jamais une recommandation.

| Début | −8 % | −6 % | −4 % | −2 % | +2 % | +4 % | +6 % | +8 % | 
|---|---|---|---|---|---|---|---|---|
| 2023-01-02 (hiver) | 3 | 4 | — | — | — | — | 2 | 2 | 
| 2023-04-03 (printemps) | 2 | 2 | 5 | — | — | — | 5 | 4 | 
| 2023-07-03 (été) | 2 | 2 | 3 | 3 | — | 3 | 2 | 2 | 
| 2023-10-02 (automne) | 2 | 2 | 2 | — | — | 6 | 2 | 2 | 

| Début | × 0,9 | × 0,8 | × 0,7 | × 0,6 | × 0,5 | 
|---|---|---|---|---|---|
| 2023-01-02 (hiver) | — | — | — | — | — | 
| 2023-04-03 (printemps) | — | — | — | — | 4 | 
| 2023-07-03 (été) | — | — | — | — | — | 
| 2023-10-02 (automne) | — | — | — | — | 6 | 

Sur 2023, un écart soutenu de ±6 % ou plus entre la demande et la prévision a donné « réentraîner » en 8 semaines au plus à chacun des 4 départs ; un écart de +2 %, à 0 des 4 départs, et de −2 %, à 1 des 4. Sur 2023, des intervalles réduits à × 0,5 de leur demi-largeur ont donné « recalibrer » à 2 des 4 départs ; aucune réduction moindre (jusqu'à × 0,9) ne l'a donné.

**Fausses alertes des épisodes courts.** Un épisode n'est pas une défaillance du modèle. On multiplie la demande par un facteur pendant 1, 3, 5 ou 7 jours, à partir de chaque jour de l'année rejouée (dès le 2023-01-02 en 2023 et le 2024-01-01 en 2024, tant que l'épisode finit au moins 14 jours avant le dernier jour jugé de l'année, pour que son écho soit jugé), et chaque cas est rejoué heure par heure par le vrai moniteur. Toute recommandation « réentraîner » ou « recalibrer » qui en sort est une fausse alerte ; elle est *sans aide* quand aucune des semaines sur lesquelles elle s'appuie n'avait déjà dépassé l'un de ses seuils de performance dans l'année telle quelle. La dernière colonne compte les départs que l'exclusion de l'écho garde de « réentraîner ».

**2023** (l'année de conception des règles) :

| Durée | Facteur | Départs | « Réentraîner » | dont sans aide | « Recalibrer » | Gardés par l'exclusion de l'écho |
|---|---|---|---|---|---|---|
| 1 j | × 0,70 | 350 | 0 (0,0 %) | 0 | 0 | 0 |
| 1 j | × 0,85 | 350 | 0 (0,0 %) | 0 | 0 | 0 |
| 1 j | × 1,15 | 350 | 0 (0,0 %) | 0 | 0 | 0 |
| 1 j | × 1,30 | 350 | 0 (0,0 %) | 0 | 0 | 0 |
| 3 j | × 0,70 | 348 | 2 (0,6 %) | 1 | 0 | 2 |
| 3 j | × 0,85 | 348 | 0 (0,0 %) | 0 | 0 | 0 |
| 3 j | × 1,15 | 348 | 0 (0,0 %) | 0 | 0 | 0 |
| 3 j | × 1,30 | 348 | 1 (0,3 %) | 0 | 0 | 0 |
| 5 j | × 0,70 | 346 | 37 (10,7 %) | 19 | 0 | 44 |
| 5 j | × 0,85 | 346 | 0 (0,0 %) | 0 | 0 | 0 |
| 5 j | × 1,15 | 346 | 0 (0,0 %) | 0 | 0 | 0 |
| 5 j | × 1,30 | 346 | 3 (0,9 %) | 2 | 0 | 35 |
| 7 j | × 0,70 | 344 | 118 (34,3 %) | 87 | 0 | 54 |
| 7 j | × 0,85 | 344 | 8 (2,3 %) | 5 | 0 | 2 |
| 7 j | × 1,15 | 344 | 1 (0,3 %) | 1 | 0 | 5 |
| 7 j | × 1,30 | 344 | 19 (5,5 %) | 16 | 0 | 75 |

**2024** (hors échantillon, mesure ajoutée après les résultats) :

| Durée | Facteur | Départs | « Réentraîner » | dont sans aide | « Recalibrer » | Gardés par l'exclusion de l'écho |
|---|---|---|---|---|---|---|
| 1 j | × 0,70 | 350 | 1 (0,3 %) | 0 | 0 | 0 |
| 1 j | × 0,85 | 350 | 1 (0,3 %) | 0 | 0 | 0 |
| 1 j | × 1,15 | 350 | 0 (0,0 %) | 0 | 5 | 0 |
| 1 j | × 1,30 | 350 | 1 (0,3 %) | 0 | 9 | 0 |
| 3 j | × 0,70 | 348 | 18 (5,2 %) | 0 | 1 | 8 |
| 3 j | × 0,85 | 348 | 6 (1,7 %) | 0 | 0 | 1 |
| 3 j | × 1,15 | 348 | 2 (0,6 %) | 0 | 6 | 0 |
| 3 j | × 1,30 | 348 | 10 (2,9 %) | 0 | 6 | 2 |
| 5 j | × 0,70 | 346 | 52 (15,0 %) | 9 | 3 | 34 |
| 5 j | × 0,85 | 346 | 13 (3,8 %) | 0 | 0 | 6 |
| 5 j | × 1,15 | 346 | 2 (0,6 %) | 0 | 6 | 0 |
| 5 j | × 1,30 | 346 | 17 (4,9 %) | 1 | 3 | 40 |
| 7 j | × 0,70 | 344 | 113 (32,8 %) | 49 | 2 | 49 |
| 7 j | × 0,85 | 344 | 30 (8,7 %) | 7 | 1 | 6 |
| 7 j | × 1,15 | 344 | 7 (2,0 %) | 0 | 6 | 5 |
| 7 j | × 1,30 | 344 | 38 (11,0 %) | 12 | 3 | 59 |

**Un choc d'un seul jour a donné une alerte**, sur 2024. Un mauvais jour ne le peut qu'en complétant des jours en échec que les semaines contenaient déjà et que la coupe écartait jusque-là ; les semaines en cause sont dans l'exécution. Un épisode de plusieurs jours en fait plus : par le retard d'un jour, il gâte un jour de plus que sa durée, et par le retard d'une semaine autant de jours la semaine suivante ; dès 3 jours, les deux semaines peuvent être hors seuil, étendues, sur la même défaillance, sans aucun jour en échec à elles. Quand le biais de l'une des deux reste dans sa bande (une bande large de ce côté, ou une semaine qui contient aussi des jours de l'épisode, comme lorsqu'il chevauche un lundi), l'exclusion de l'écho ne joue pas. Les statistiques hebdomadaires ne distinguent pas un tel épisode de deux semaines de défaillance du modèle ; les règles restent telles quelles, et ces tableaux en donnent le prix.

**Changement de niveau de la demande.** La demande est multipliée par un facteur à partir de la première semaine jugée de janvier, avril, juillet et octobre, pendant 10 semaines (semaines avant « réentraîner » ; — : rien en 10 semaines). La prévision par persistance suit la demande par ses retards : un changement de niveau ne laisse entre la prévision et la demande qu'un écart bien plus petit que lui-même. Le tableau de puissance mesure cet écart ; les deux tableaux qui suivent mesurent le changement de niveau.

**2023** (l'année de conception des règles) :

| Début | −10 % | −5 % | +5 % | +10 % | 
|---|---|---|---|---|
| 2023-01-02 (hiver) | — | — | — | — | 
| 2023-04-03 (printemps) | 2 | — | — | — | 
| 2023-07-03 (été) | 2 | 4 | — | — | 
| 2023-10-02 (automne) | 2 | — | — | — | 

**2024** (hors échantillon, mesure ajoutée après les résultats) :

| Début | −10 % | −5 % | +5 % | +10 % | 
|---|---|---|---|---|
| 2024-01-01 (hiver) | — | — | — | — | 
| 2024-04-01 (printemps) | — | — | — | 10 | 
| 2024-07-01 (été) | 3 | — | — | — | 
| 2024-10-07 (automne) | 2 | — | — | — | 

**Ce que coûte l'exclusion de l'écho.** À partir de la première semaine jugée de janvier, avril, juillet et octobre, pendant 8 semaines, la prévision reçoit une erreur aléatoire, tirée chaque jour selon une loi normale : c'est un modèle dégradé dont le biais hebdomadaire peut changer de côté. Les règles sont appliquées avec et sans l'exclusion.

**2023** (l'année de conception des règles) :

| Écart-type quotidien | Essais | « Réentraîner » avec l'exclusion | sans l'exclusion | Supprimés par l'exclusion | Retardés |
|---|---|---|---|---|---|
| 6 % | 80 | 19 | 21 | 2 | 0 |
| 10 % | 80 | 44 | 44 | 0 | 8 |
| 15 % | 80 | 74 | 76 | 2 | 13 |

**2024** (hors échantillon, mesure ajoutée après les résultats) :

| Écart-type quotidien | Essais | « Réentraîner » avec l'exclusion | sans l'exclusion | Supprimés par l'exclusion | Retardés |
|---|---|---|---|---|---|
| 6 % | 80 | 15 | 16 | 1 | 1 |
| 10 % | 80 | 42 | 46 | 4 | 7 |
| 15 % | 80 | 71 | 74 | 3 | 13 |


![Rejeu de 2023](figures/validation/pseudo-replay-2023-weekly.fr.png)

## 7. 2024 rejouée comme en production

**A posteriori.** Le rejeu de 2024 a tourné à nouveau, après une dérogation aux règles gelées : les règles ont été changées après que les résultats de 2024 eurent été vus. La dérogation est consignée dans `AGENTS.md`, à la section « Departures from the frozen monitoring rules », qui en donne la liste ; les règles gelées d'abord sont celles du commit `7ad8b71088fd`, les règles après la dérogation celles du commit `affe089f6ed0`. Les résultats de cette section sont ceux du rejeu après la dérogation ; le rejeu d'avant est à côté.

| | Rejeu d'avant, règles du commit `7ad8b71088fd` (exécution `91f7e3faecee4a899381edd47b4ffc3f`) | Rejeu après la dérogation, règles du commit `affe089f6ed0` |
|---|---|---|
| EAMP | 4,23 % | 4,23 % |
| Couverture | 89,4 % | 89,4 % |
| Recommandations | aucune | aucune |

La dérogation du 2026-10-14 porte sur ces points :

- Une valeur mal placée n'est replacée que si sa destination est sans ambiguïté : la destination suivante en coût doit coûter au moins le rapport minimal fois la destination choisie (rapport de qualité des données, section 3.2).
- Une valeur en double hors des bornes physiques ne peut servir à aucune réparation, et aucune heure dont la valeur sort de ces bornes n'est rendue utilisable.
- Le code de conception (plis de validation, seuils, modèles figés) voit chaque moment de décision comme le rejeu voit son horloge, et la demi-largeur d'un intervalle n'est calibrée que sur les jours déjà notés quand son modèle est ajusté (section 8).
- La garde contre l'information future perd son terme de 168 heures, qui ne pouvait jamais se déclencher seul.
- Les seuils sont calibrés sur les semaines que le rejeu juge : une semaine qui contient un jour en échec de qualité, ou dont les jours ne sont pas tous dans la période, n'y entre plus (section 5).
- La porte du gel s'applique dans le rejeu lui-même, si bien qu'aucun point d'entrée ne rejoue 2024 sans elle.
- La vérification de l'étiquette déplacée lit aussi le registre des rejeux du dépôt (`docs/replay-ledger.json`), si bien qu'une copie neuve du dépôt voit une dérogation.
- Les fausses alertes et les angles morts des règles sont aussi mesurés sur 2024, hors échantillon (section 6).


L'horloge avance d'heure en heure du 2024-01-01 au 2024-12-31 ; chaque lecture passe par un accès qui refuse toute donnée postérieure à l'horloge. Le modèle et ses intervalles sont vérifiés par empreinte avant et après : `c8e1725aad77`, inchangée.

| | |
|---|---|
| EAMP | 4,23 % [3,70 % ; 4,84 %] |
| Biais | +0,31 % [−0,28 % ; +0,91 %] |
| Couverture (nominale 90 %) | 89,4 % [85,1 % ; 92,9 %] |
| Heures de l'année / prévues / notées | 8 784 / 8 760 / 8 758 |
| Jours en échec de qualité | 0 sur 366 |
| Semaines jugées | 52 sur 53 |

Heures non prévues, par donnée absente (une heure peut en manquer plusieurs) : demande de la semaine précédente indisponible (2), demande de la veille indisponible (2), température de la veille absente (20).

**Recommandations.**

Aucune semaine de 2024 n'a déclenché de recommandation.

**Avec la première formulation de la règle 2.** Elle dit « réentraîner » la semaine du 2024-11-04 (la semaine d'avant : EAMP 6,30 % hors seuil, biais +0,75 % ; celle-ci : EAMP 6,36 % hors seuil, biais −0,86 %). Semaine du 2024-11-04 : l'EAMP est au-delà de son seuil les deux semaines, mais, sans les 2 jours les plus extrêmes de chaque semaine, vaut 4,71 % puis 4,64 % pour un seuil de 6,00 % : aucune des deux semaines n'est hors seuil, étendue ; la règle retenue n'y voit donc rien. Le biais change de signe d'une semaine à l'autre, mais reste dans sa bande les deux semaines : ce n'est pas l'exclusion de l'écho qui joue.

**Puissance des règles sur 2024**, mesurée de la même façon (semaines avant « réentraîner », puis avant « recalibrer » ; — : rien en 8 semaines) :

| Début | −8 % | −6 % | −4 % | −2 % | +2 % | +4 % | +6 % | +8 % | 
|---|---|---|---|---|---|---|---|---|
| 2024-01-01 (hiver) | 3 | 6 | — | — | — | — | 3 | 2 | 
| 2024-04-01 (printemps) | 2 | 2 | 6 | — | — | 8 | 3 | 2 | 
| 2024-07-01 (été) | 2 | 2 | 2 | — | — | 2 | 2 | 2 | 
| 2024-10-07 (automne) | 2 | 2 | 2 | 3 | — | — | 3 | 2 | 

| Début | × 0,9 | × 0,8 | × 0,7 | × 0,6 | × 0,5 | 
|---|---|---|---|---|---|
| 2024-01-01 (hiver) | — | — | — | — | 3 | 
| 2024-04-01 (printemps) | — | — | — | — | 2 | 
| 2024-07-01 (été) | — | — | — | — | — | 
| 2024-10-07 (automne) | 5 | 5 | 5 | 3 | 3 | 

Sur 2024, un écart soutenu de ±6 % ou plus entre la demande et la prévision a donné « réentraîner » en 8 semaines au plus à chacun des 4 départs ; un écart de +2 %, à 0 des 4 départs, et de −2 %, à 1 des 4. Sur 2024, des intervalles réduits à × 0,5 de leur demi-largeur ont donné « recalibrer » à 3 des 4 départs ; des réductions moindres (jusqu'à × 0,9), à 1 des 4 départs au plus.

![Rejeu de 2024](figures/validation/replay-2024-weekly.fr.png)

![Dérive 2024](figures/validation/replay-2024-drift.fr.png)

Pour chaque semaine en alarme de dérive, le rapport Evidently est conservé dans l'exécution, et la valeur d'Evidently est comparée à celle du moniteur : 4 comparaisons, toutes égales.

## 8. Limites

- **Couverture conditionnelle.** L'intervalle de production sous-couvre l'hiver ; les déciles de charge prévue 7, 8, 9 et 10 (section 4). Les seuils de couverture de l'hiver sont tirés de semaines qui avaient déjà cette sous-couverture : le moniteur ne la signale pas comme une dégradation. À l'inverse, le seuil de couverture de l'été (96,4 %) dépasse le nominal de 90 % : une semaine couverte au-delà du nominal peut y être signalée basse. Les seuils mesurent l'écart à la couverture de chaque saison en validation, pas au nominal.
- **Froids extrêmes.** La pointe de 2023-02 n'est pas couverte (0 des 5 heures les plus chargées) ; même avec la météo observée, le modèle la sous-estime, et la cause n'est pas testée. Une seule pointe est étudiée ; les vagues de froid ne sont pas mesurées comme catégorie.
- **Épisodes de plusieurs jours** (mesuré sur 2023, l'année de conception des règles, et sur 2024, hors échantillon, mesure ajoutée après les résultats). Un épisode de plusieurs jours peut donner « réentraîner » sans que le modèle soit en cause : sur 2023, jusqu'à 34,3 % des départs pour 7 jours à × 0,70, et au plus 2,3 % pour un épisode de ±15 % ; sur 2024, jusqu'à 32,8 % des départs pour 7 jours à × 0,70, et au plus 8,7 % pour un épisode de ±15 % (section 6).
- **Changement de niveau** (mesuré sur 2023, l'année de conception des règles, et sur 2024, hors échantillon, mesure ajoutée après les résultats). Sur 2023, une hausse soutenue de 10 % de la demande n'a pas donné « réentraîner » en 10 semaines à 4 départs sur 4 (janvier, avril, juillet et octobre), et une baisse de 10 % l'a donné à 3 sur 4. Sur 2024, une hausse soutenue de 10 % de la demande n'a pas donné « réentraîner » en 10 semaines à 3 départs sur 4 (janvier, avril, juillet et octobre), et une baisse de 10 % l'a donné à 2 sur 4. La persistance absorbe l'essentiel d'une hausse par ses retards. Sur 2023 et 2024, une baisse a donné « réentraîner » plus souvent qu'une hausse : elle laisse un écart relatif plus grand qu'une hausse de même taille, car le biais se mesure contre la demande plus basse ; la position du biais hebdomadaire de l'année dans la bande de chaque saison fait le reste. Les tableaux de puissance mesurent un écart entre prévision et demande, pas un changement de niveau.
- **Exclusion de l'écho** (mesuré sur 2023, l'année de conception des règles, et sur 2024, hors échantillon, mesure ajoutée après les résultats). Sur un modèle dégradé dont le biais change de côté, elle supprime « réentraîner » sur 2023 dans 4 essais sur 240 et le retarde dans 21, et sur 2024 dans 8 essais sur 240 et le retarde dans 21 (section 6) ; c'est le prix d'éviter les fausses alertes d'un épisode suivi de son écho.
- **Puissance de « recalibrer ».** Des intervalles réduits à × 0,5 de leur demi-largeur n'ont pas donné « recalibrer » en 8 semaines à 2 des 4 départs de 2023, et à × 0,5 à 1 des 4 départs de 2024 (sections 6 et 7) : la règle 3 demande une couverture sous le seuil de la saison, étendue, 2 semaines de suite, avec un biais dans sa bande.
- **Fausses alertes de dérive en 2024.** Les niveaux d'alarme sont le 95ᵉ centile des semaines de validation 2021–2023, dont les références couvraient de 2 à 4 années antérieures pour la température et de 1 à 3 pour la prévision ; en 2024, elles en couvrent 5 et 4. Le taux de fausses alertes de dérive en 2024 n'est pas mesuré.
- **Règle 4 et un vrai changement de température.** Un écart de température réel et soutenu, auquel la demande répond et que le modèle suit, performance dans ses seuils, peut donner « examiner les données » : la règle ne distingue pas un vrai changement de la météo d'une erreur de données (bris « Une dérive de température, performance dans les seuils : examiner les données », section 9). La règle reste telle quelle.
- **Qualité des données en 2024.** Le rejeu juge chaque jour sur les enregistrements bruts coupés à l'horloge, mais il ne tourne que si toute la fenêtre, 2024 comprise, a passé les portes du rapport de qualité. Les valeurs hors bornes, les enregistrements hors grille et les températures en double ne pouvaient donc pas apparaître dans le rejeu ; le moniteur quotidien y jugeait encore les heures manquantes, les valeurs vides, les doublons de demande et le silence de la station principale.
- **Données en temps réel.** Les valeurs sont celles téléchargées le 2026-10-14 (demande) et à partir du 2026-10-14 (météo) ; ni délai de publication ni révision ne sont modélisés. La conception voit chaque moment de décision comme le rejeu voit son horloge : la série de demande y est reconstruite à partir des seuls enregistrements bruts datés de ce moment ou d'avant (pour l'ajustement d'un modèle, l'origine de son premier jour testé ; pour les retards, l'origine de chaque jour ; pour la cible, le moment où le jour est noté). La réparation qui comble l'heure manquante du 2022-01-01 05:00 UTC avec un enregistrement daté du 2023-01-01 05:00 UTC n'entre donc dans la conception qu'après l'arrivée de cet enregistrement, au même moment que dans les rejeux. La demi-largeur d'un intervalle, fixée à l'ajustement de son modèle, n'est calibrée que sur les jours déjà notés à ce moment (un jour est noté une heure après sa dernière heure, ou à la fin des données de conception) : la veille du premier jour testé d'un mois de validation en reste exclue.
- **Indépendance.** Le modèle, les contrôles et ce rapport viennent d'une même session d'agent IA sous la direction d'un seul propriétaire, sans revue humaine indépendante du code. La revue a été faite par des agents à contexte neuf, ce qui n'est pas une validation indépendante au sens de SR 11-7.

## 9. Les bris, sur des données synthétiques

Chaque porte et chaque moniteur a un test qui injecte la faute qu'il doit attraper ; un témoin n'injecte rien et doit rester silencieux. L'exécution `breaks` les lance tous ; le rapport refuse d'être généré si l'un d'eux a échoué. Aucun bris ne lit les vraies données de demande ou de météo : les portes de qualité, les gardes et le rendu sont testés sur de petites données fabriquées, et l'horloge et les moniteurs dans un monde synthétique (`tests/replay_world.py`) : un peu plus de deux ans de demande fabriquée, un modèle figé dessus, des seuils tirés de ses propres semaines hors échantillon par la même règle des quantiles que les vrais (un seul jeu pour les quatre saisons), et un niveau d'alarme de dérive fixé à la main à 1,0, non par cette règle. Qu'un bris se déclenche dans ce monde montre que le contrôle peut se déclencher, pas qu'il verrait la même faute dans la vraie demande.

| Contrôle | Bris | Résultat |
|---|---|---|
| Portes de qualité des données | Un doublon de forme inconnue fait échouer la porte | déclenché |
| Portes de qualité des données | Un enregistrement mal placé mais ambigu n'est pas déplacé et fait échouer la porte | déclenché |
| Portes de qualité des données | Un enregistrement hors de la fenêtre fait échouer la porte | déclenché |
| Portes de qualité des données | Une demande hors bornes fait échouer la porte | déclenché |
| Portes de qualité des données | Une couverture insuffisante fait échouer la porte | déclenché |
| Portes de qualité des données | Des lignes météo en double font échouer la porte d'index | déclenché |
| Portes de qualité des données | Une température impossible fait échouer la porte des bornes | déclenché |
| Portes de qualité des données | Une panne de capteur fait échouer la porte de couverture | déclenché |
| Portes de qualité des données | Une panne commune à plusieurs stations fait échouer la porte du panel | déclenché |
| Portes de qualité des données | Une porte en échec arrête la chaîne : aucune donnée canonique n'est écrite | déclenché |
| Portes de qualité des données | Des fichiers bruts qui ne correspondent plus à leur manifeste sont refusés | déclenché |
| Portes de qualité des données | Un enregistrement hors de la grille horaire fait échouer la porte, avec son horodatage | déclenché |
| Portes de qualité des données | Une valeur hors bornes cachée dans une paire du retour à l'heure normale fait quand même échouer la porte | déclenché |
| Portes de qualité des données | Une valeur hors bornes est écartée avant qu'une réparation ne la lise | déclenché |
| Portes de qualité des données | Un enregistrement mal placé qui irait aussi bien dans deux heures vides n'est pas déplacé et fait échouer la porte | déclenché |
| Portes de qualité des données | Un doublon hors bornes n'est jamais déplacé dans une heure utilisable, ni dans le rejeu | déclenché |
| Portes de qualité des données | Une valeur hors bornes qui atteint une heure utilisable est marquée hors bornes | déclenché |
| Portes de qualité des données | Une valeur hors bornes ne crée pas de pic chez ses voisines | déclenché |
| Pas d'information future | Une entrée postérieure à l'origine de la prévision est refusée | déclenché |
| Pas d'information future | Une ligne d'entraînement dans l'embargo est refusée | déclenché |
| Pas d'information future | Une ligne de 2024 est refusée par le code de conception | déclenché |
| Pas d'information future | La validation refuse une ligne de 2024 | déclenché |
| Pas d'information future | Une référence de dérive contenant des jours rejoués est refusée | déclenché |
| Pas d'information future | Des seuils tirés de l'année rejouée sont refusés | déclenché |
| Pas d'information future | Les seuils de dérive d'un rejeu ignorent l'année rejouée | déclenché |
| Pas d'information future | Le modèle figé refuse des lignes d'entraînement dans son embargo | déclenché |
| Pas d'information future | Le verdict de qualité des jours qui sert au calibrage ne lit aucun enregistrement postérieur au dernier jour | déclenché |
| Pas d'information future | Une origine avancée au-delà de ce que permet le retard de 24 heures est refusée | déclenché |
| Pas d'information future | Une valeur réparée avec un enregistrement arrivé plus tard n'entre ni dans les retards ni dans l'entraînement avant son arrivée | déclenché |
| Pas d'information future | Une demi-largeur fixée à l'ajustement d'un modèle n'est calibrée que sur les jours déjà notés à ce moment, pas sur une valeur réparée une heure plus tard | déclenché |
| Pas d'information future | Des erreurs hors échantillon qui ne disent pas quand elles ont été notées ne calibrent aucune demi-largeur | déclenché |
| Horloge du rejeu | Des données corrompues après un instant ne changent rien de ce qui a été jugé avant | déclenché |
| Horloge du rejeu | Un enregistrement arrivé une semaine en retard n'est pas utilisé avant son arrivée | déclenché |
| Horloge du rejeu | Lire au-delà de l'horloge, ou la faire reculer, est refusé | déclenché |
| Horloge du rejeu | Une prévision émise après son origine est refusée | déclenché |
| Horloge du rejeu | Un jour noté avant l'heure qui suit sa dernière heure est refusé | déclenché |
| Horloge du rejeu | Une prévision bâtie sur une ligne d'une autre origine est refusée | déclenché |
| Horloge du rejeu | Une prévision bâtie sur une ligne d'un autre jour est refusée | déclenché |
| Moniteurs et table de décision | Témoin : des données propres ne donnent aucune recommandation | resté silencieux |
| Moniteurs et table de décision | La référence saisonnière reste calme sur des données propres et réagit à un décalage | déclenché |
| Moniteurs et table de décision | Une dérive de température, performance dans les seuils : examiner les données | déclenché |
| Moniteurs et table de décision | Un trou de capteur fait échouer la qualité du jour et suspend les règles de performance | déclenché |
| Moniteurs et table de décision | Un enregistrement manquant et un doublon font échouer leur jour | déclenché |
| Moniteurs et table de décision | Une semaine basse (−30 %) et son écho par le retard d'une semaine ne donnent pas « réentraîner » | déclenché |
| Moniteurs et table de décision | Une semaine haute (+30 %) et son écho, EAMP haute les deux semaines, ne donnent pas « réentraîner » | déclenché |
| Moniteurs et table de décision | Une EAMP haute deux semaines avec un biais de signes opposés est un écho ; sans inversion, elle déclenche | déclenché |
| Moniteurs et table de décision | Un biais du même côté trois semaines de suite donne « réentraîner » ; un écho ou une semaine non jugée l'interrompt | déclenché |
| Moniteurs et table de décision | La mesure de puissance trouve un écart soutenu de 8 % ou des intervalles réduits à 20 %, et rien sans changement | déclenché |
| Moniteurs et table de décision | La mesure des changements de niveau trouve une hausse de 12 % de la demande, et rien sans changement | déclenché |
| Moniteurs et table de décision | La mesure des épisodes : un jour de choc ne donne aucune fausse alerte, et chaque départ est compté | déclenché |
| Moniteurs et table de décision | La mesure des épisodes trouve la fausse alerte de cinq jours qui chevauchent un lundi, et l'écho que l'exclusion écarte | déclenché |
| Moniteurs et table de décision | Une fausse alerte appuyée sur une semaine déjà hors bande est dite aidée ; une alerte d'origine n'est pas comptée | déclenché |
| Moniteurs et table de décision | L'exclusion de l'écho ne fait jamais qu'enlever ou retarder « réentraîner » | déclenché |
| Moniteurs et table de décision | Deux jours altérés ne portent jamais la statistique tronquée au-delà des jours intacts (2 000 tirages) | déclenché |
| Moniteurs et table de décision | Les semaines incomplètes n'ont pas de statistique de dérive | déclenché |
| Moniteurs et table de décision | Le dernier jour est jugé à la fin des données, et le dit | déclenché |
| Moniteurs et table de décision | Des températures en double et un enregistrement hors grille font échouer leur jour | déclenché |
| Moniteurs et table de décision | Un changement de niveau de la demande : réentraîner | déclenché |
| Moniteurs et table de décision | Des intervalles trop étroits : recalibrer | déclenché |
| Moniteurs et table de décision | Deux règles à la fois : une recommandation, les deux preuves | déclenché |
| Moniteurs et table de décision | Un choc d'un jour (samedi) ne donne pas « réentraîner » | déclenché |
| Moniteurs et table de décision | Un choc d'un jour (dimanche) ne donne pas « réentraîner » | déclenché |
| Moniteurs et table de décision | Une semaine de trop peu d'heures notées n'est pas jugée | déclenché |
| Moniteurs et table de décision | Une semaine de trop peu de jours complets n'a pas de statistique tronquée et n'est jamais hors bande étendue | déclenché |
| Moniteurs et table de décision | Une semaine incomplète, une semaine avec un jour en échec et une semaine de trop peu d'heures ne sont pas jugées, chaque condition à elle seule | déclenché |
| Moniteurs et table de décision | Une semaine coupée par la fin de la période n'est pas jugée, même avec assez d'heures | déclenché |
| Moniteurs et table de décision | Le verdict de qualité des jours lu par le calibrage est, jour par jour, celui du rejeu, sur sept défauts injectés et les jours propres | déclenché |
| Moniteurs et table de décision | Une semaine contenant un jour que le rejeu ferait échouer ne sert pas au calcul des seuils, et la raison est consignée | déclenché |
| Moniteurs et table de décision | Une semaine coupée par les années de calibrage est écartée ; un jour sans verdict de qualité fait refuser le calcul des seuils | déclenché |
| Le moniteur n'agit jamais | Un rejeu qui modifie le modèle ou ses intervalles échoue | déclenché |
| Règles gelées | Sans l'étiquette de gel, le rejeu de 2024 est refusé | déclenché |
| Règles gelées | Un fichier de règles modifié après le gel est refusé et nommé | déclenché |
| Règles gelées | Un fichier de règles ajouté après le gel est refusé | déclenché |
| Règles gelées | Une étiquette de gel déplacée est refusée sauf écart déclaré | déclenché |
| Règles gelées | Le rejeu appelé directement sur l'année réservée passe la porte du gel : sans étiquette, avec un fichier de règles modifié ou sous une étiquette déplacée, il est refusé | déclenché |
| Règles gelées | Le registre versionné des rejeux révèle une étiquette déplacée même sans dépôt MLflow : refus sauf dérogation consignée | déclenché |
| Règles gelées | Un registre des rejeux illisible est refusé | déclenché |
| Règles gelées | Un registre des rejeux sans liste de rejeux est refusé | déclenché |
| Règles gelées | Une entrée du registre sans commit de gel est refusée | déclenché |
| Règles gelées | Les rejeux antérieurs sont retrouvés même supprimés | déclenché |
| Règles gelées | Des fichiers bruts autres que ceux contrôlés sont refusés | déclenché |
| Règles gelées | La conception refuse un fichier de demande brut autre que celui contrôlé | déclenché |
| Règles gelées | Un modèle enregistré qui change pendant le rejeu le fait échouer | déclenché |
| Règles gelées | Un modèle figé d'une autre validation, d'une autre année ou d'autres règles est refusé | déclenché |
| Règles gelées | Une validation, ou un contrôle de qualité, fait sur d'autres données ou d'autres règles est refusé | déclenché |
| Règles gelées | Un fichier de modèle figé qui ne correspond plus à son empreinte est refusé | déclenché |
| Règles gelées | Le modèle enregistré est refusé si son exécution porte une autre empreinte | déclenché |
| Règles gelées | Sans l'étiquette de gel, le rejeu de 2024 s'arrête avant de lire la moindre donnée | déclenché |
| Règles gelées | Sous une étiquette de gel déplacée, le rejeu de 2024 s'arrête avant de lire la moindre donnée, même avec --departure sans dérogation consignée | déclenché |
| Règles gelées | Le rejeu de 2024 refuse un modèle figé construit sur une autre validation | déclenché |
| Règles gelées | Le rejeu de 2024 échoue si le modèle enregistré change pendant qu'il tourne | déclenché |
| Règles gelées | Un alias qui pointe vers le modèle figé d'une autre année est refusé | déclenché |
| Rendu des rapports | Un rapport dont le code a changé depuis l'exécution est refusé | déclenché |
| Rendu des rapports | L'analyse de sécurité publique réagit à un contenu privé | déclenché |
| Rendu des rapports | Une figure dont les métadonnées portent un chemin privé, ou suivie d'octets cachés, est refusée | déclenché |
| Rendu des rapports | Un fichier retiré plus tard de l'arbre, un chemin interdit ou une référence oubliée reste repéré dans l'historique | déclenché |
| Rendu des rapports | Le message d'une étiquette annotée portant un chemin privé est repéré | déclenché |
| Rendu des rapports | Les raccourcis du répertoire personnel réagissent, pas la prose ordinaire | déclenché |
| Rendu des rapports | Une figure portant un bloc hors liste ou une somme de contrôle fausse est refusée | déclenché |
| Rendu des rapports | Une figure dont la structure enfreint les règles du PNG est refusée : octets dans le bloc de fin, bloc de taille fixe trop long, en-tête absent, bloc en double, données d'image absentes, coupées ou qui ne se décompressent pas exactement à la taille que l'en-tête annonce | déclenché |
| Rendu des rapports | Un chemin privé caché dans un bloc de taille fixe d'une figure est repéré | déclenché |
| Rendu des rapports | Témoin : une chaîne d'exécutions intacte est suivie jusqu'au bout | resté silencieux |
| Rendu des rapports | Une exécution des bris marquée en échec rompt la chaîne du rapport | déclenché |
| Rendu des rapports | Une exécution faite avec un autre code rompt la chaîne du rapport | déclenché |
| Rendu des rapports | Un modèle figé pour une autre année rompt la chaîne du rapport | déclenché |
| Rendu des rapports | Un rejeu fait sous un autre commit de gel rompt la chaîne du rapport | déclenché |
| Rendu des rapports | Une validation qui désigne un autre contrôle de qualité rompt la chaîne du rapport | déclenché |
| Rendu des rapports | Un rejeu fait après une dérogation apporte les rejeux qu'il remplace ; s'il n'en nomme aucun, il est refusé | déclenché |
| Rendu des rapports | Une dérogation consignée dans `AGENTS.md` que le rejeu de 2024 ne porte pas rompt la chaîne du rapport, de même qu'un rejeu après une dérogation dont `AGENTS.md` ne nomme pas le commit de gel | déclenché |
| Rendu des rapports | Témoin : sans dérogation consignée, une chaîne intacte passe ; avec une dérogation, le rejeu qui la porte passe | resté silencieux |
| Rendu des rapports | Une dérogation consignée par un hachage de moins de 12 caractères rompt la chaîne du rapport | déclenché |
| Rendu des rapports | Une section des dérogations introuvable dans `AGENTS.md` rompt la chaîne du rapport | déclenché |
| Rendu des rapports | Une saison absente de la couverture du run de validation fait refuser le rapport | déclenché |
| Rendu des rapports | Une semaine de la première formulation de la règle 2 absente des semaines jugées du rejeu fait refuser le rapport | déclenché |
| Rendu des rapports | Une défaillance étendue aux deux semaines sans « réentraîner » dans le rejeu fait refuser le rapport | déclenché |
| Rendu des rapports | Une exécution des bris qui ne marque pas ses témoins fait refuser le rapport | déclenché |
| Rendu des rapports | Un cas du printemps 2020 sans heures de printemps fait refuser le rapport | déclenché |
| Rendu des rapports | Un tableau de puissance vide fait refuser le rapport | déclenché |
| Rendu des rapports | Un tableau de puissance sans lignes d'intervalles réduits fait refuser le rapport | déclenché |
| Rendu des rapports | Un intervalle de la différence du printemps 2020 dont deux tirages partagent une graine, ou fait d'un seul lot, fait refuser le rapport | déclenché |
| Rendu des rapports | Une borne de l'intervalle de la différence que l'erreur de Monte-Carlo ou l'arrondi ne sépare pas de zéro laisse le verdict indéterminé | déclenché |
| Rendu des rapports | L'adresse d'une session d'agent dans un message de commit, un message d'étiquette ou un fichier indexé est repérée | déclenché |
| Rendu des rapports | Une entrée du registre des rejeux réécrite à la main fait refuser la mise à jour du registre | déclenché |
| Rendu des rapports | Un registre des rejeux portant un nom privé, ou un champ hors de sa forme fixe, n'est pas écrit | déclenché |
| Rendu des rapports | Un rejeu de 2024 qui ne concorde pas avec son entrée du registre rompt la chaîne du rapport | déclenché |
| Rendu des rapports | Un rejeu d'avant une dérogation absent du magasin et du registre, ou consigné comme incomplet, rompt la chaîne du rapport | déclenché |
| Rendu des rapports | Témoin : un rejeu d'avant une dérogation absent du magasin est montré d'après le registre, avec la mention « consigné dans le registre, non rejoué ici » | resté silencieux |
| Rendu des rapports | Un rejeu d'avant une dérogation qui ne concorde pas avec son entrée du registre rompt la chaîne du rapport | déclenché |
| Rendu des rapports | Un registre des rejeux modifié ou supprimé après le rendu fait échouer le test de l'estampille du rapport | déclenché |
| Rendu des rapports | Un bris qui ne s'est pas déclenché met l'exécution des bris en échec | déclenché |
| Rendu des rapports | Une borne de l'intervalle de la différence imprimée à au plus k erreurs types imprimées de zéro laisse le verdict indéterminé, et une borne imprimée plus loin ne le laisse jamais : le verdict ne contredit pas ses nombres | déclenché |
| Rendu des rapports | Une borne de l'intervalle de la différence imprimée à au plus k erreurs types imprimées de zéro laisse le verdict indéterminé, et une borne imprimée plus loin ne le laisse jamais : le verdict ne contredit pas ses nombres | déclenché |
| Pas d'information future | Une ligne notée une heure après l'horloge d'ajustement ne calibre pas la demi-largeur ; une ligne notée à l'horloge même la calibre | déclenché |
| Rendu des rapports | Un rejeu de 2024 sans ses mesures de fausses alertes et d'angles morts fait refuser le rapport | déclenché |
| Rendu des rapports | Des mesures de 2024 faites avec d'autres durées, facteurs, changements de niveau ou bruits que celles de 2023 font refuser le rapport | déclenché |
| Rendu des rapports | Un rejeu de 2024 qui déroge à des rejeux faits sous deux commits de gel, une dérogation que le rapport ne décrit pas, fait refuser le rapport | déclenché |

Le bris « Un changement de niveau de la demande : réentraîner » multiplie la demande du monde synthétique par 1,12, une hausse de 12 %. Sur les vraies heures de 2023, une hausse de 10 % n'a pas donné « réentraîner » en 10 semaines à 4 des 4 départs (section 8) : la règle peut se déclencher, mais elle n'a vu une telle hausse dans la vraie demande à aucun départ. Sur les vraies heures de 2024 (hors échantillon, mesure ajoutée après les résultats), la même hausse n'a pas donné « réentraîner » en 10 semaines à 3 des 4 départs (section 8).

## 10. Correspondance avec SR 11-7 et le NIST AI RMF

Les citations, avec leurs sections et pages, sont dans `docs/standards.md` (en anglais, langue des documents). Le tableau dit ce que les contrôles couvrent, rien de plus.

| Attente | Ce que fait valiwatt | Où c'est vérifié |
|---|---|---|
| SR 11-7, solidité conceptuelle, y compris l'analyse de sensibilité | Configuration fixée dans le plan avant la validation (la source de température, après la comparaison des stations du rapport de qualité sur 2019–2023) ; variables nommées, coefficients montrés à côté d'un ajustement sans retards. Des spécifications de rechange : moyenne des 6 stations, intervalle absolu, quantile par saison, première formulation de la règle 2. Ce ne sont pas les petites variations des entrées et des paramètres que SR 11-7 appelle analyse de sensibilité ; celles-ci ne sont pas faites. | Plan, section 2 ; exécutions de validation et du modèle figé |
| SR 11-7, qualité et pertinence des données | 23 portes de qualité sur toute la fenêtre, chaque réparation documentée ; représentativité de la station ; en production, un moniteur de qualité quotidien sur les enregistrements bruts. | Rapport de qualité des données ; rejeux |
| SR 11-7, surveillance continue : vérification du processus | Gardes contre l'information future ; horloge qui refuse les données postérieures ; empreinte du modèle vérifiée avant et après ; le rejeu de 2024 refuse de tourner si les règles diffèrent de l'étiquette `rules-frozen` ; l'étiquette a été déplacée une fois, par une dérogation consignée dans `AGENTS.md`, et le rejeu d'avant est montré à côté (section 7). Chaque garde a son bris. | Section 9 |
| SR 11-7, surveillance continue : étalonnage comparatif | Référence naïve ; météo observée comme borne optimiste ; modèle figé contre plis réajustés sur 2023 (analyse de résultats en parallèle). | Sections 2 et 6 |
| SR 11-7, analyse des résultats et contrôle a posteriori, avec intervalles | Plis mensuels avec embargo ; intervalles à 90 % de forme conforme, sans garantie en échantillon fini, dont la couverture est mesurée globalement, par saison, par bloc horaire et par décile ; statistiques hebdomadaires jugées contre des seuils fixés d'avance. Au sens de SR 11-7, une période qui n'a pas servi au développement, seul le rejeu de 2024 est un contrôle a posteriori : les années de validation, 2020–2023, sont parmi celles (2019–2023) sur lesquelles la station a été choisie, et 2023 a servi à la conception des règles. Depuis la dérogation, ce contrôle est le rejeu d'avant, sur les règles gelées avant 2024 (section 7) : les règles du rejeu après la dérogation ont été changées après que ses résultats eurent été vus. | Sections 2, 4, 6, 7 |
| SR 11-7, seuils d'acceptabilité, puis recalibrage ou refonte | Seuils par saison tirés des semaines antérieures ; table de décision ordonnée (examiner, réentraîner, recalibrer), avec preuves ; recommande sans agir. | Sections 5 et 7 |
| SR 11-7, documentation et rapports de validation | Ce rapport et sa fiche du modèle, générés depuis les exécutions et estampillés de leurs identifiants et de l'empreinte du code. | Ce rapport |
| NIST MAP 2.2, 2.3 | Usage prévu, hors champ, mise en garde météo, limites ; plan de validation et constats de qualité documentés. | Fiche du modèle ; plan |
| NIST MEASURE 1.1, 2.1, 2.3, 2.4, 2.5, 2.13 | Exécutions MLflow (jeux de test, métriques, paramètres, versions) ; rejeux dans des conditions de production ; bris ; la liste « non couvert » ci-dessous. | Exécutions ; section 9 |
| NIST MANAGE 2.2 | Recommandations hebdomadaires avec preuves ; qui agit, et comment, est hors champ. | Section 7 |

**Non couvert.** Validation indépendante et remise en question effective (SR 11-7, V ; NIST MEASURE 1.3) ; gouvernance, politiques et contrôles, audit interne, inventaire des modèles (SR 11-7, VI ; NIST GOVERN pour l'essentiel) ; revue périodique ; analyse des dérogations ; usage dans des décisions et matérialité ; revue indépendante d'un recalibrage ; analyse de sensibilité au sens de SR 11-7 (petites variations des entrées et des paramètres) ; NIST MANAGE 2.4 (qui remplace, désengage ou désactive le modèle) ; NIST MANAGE 4.1 et 1.4 au-delà des limites de la fiche ; données en temps réel.

## 11. Provenance

| Exécution | Identifiant | Date |
|---|---|---|
| qualité des données | `021a47bc77824742bef41d8731c2d951` | 2026-10-15 01:50 UTC |
| validation | `b7268bf5603f47a5a190c716274993ee` | 2026-10-15 01:51 UTC |
| modèle figé pour 2023 | `04160feea3cf4ff583b177c5883c5735` | 2026-10-15 01:51 UTC |
| modèle figé pour 2024 | `2823ecf3be7b4e8ca0b410fcfc16b05f` | 2026-10-15 02:22 UTC |
| rejeu de 2023 | `ef636177ec6c4656bebcd8bc3f919d81` | 2026-10-15 02:22 UTC |
| rejeu de 2024 | `aff7424cfb204711aae2bdcd0a442c68` | 2026-10-15 02:53 UTC |
| bris | `669c16273b244634b4472dd5148374d5` | 2026-10-15 02:56 UTC |

Empreinte du code : `6894ad412fe0d6b6c2bdd68edbd6d8fe4ae4b86eb3afe909ad5ece9b765a36b9`. Empreinte des règles : `e0b993967d938d40af6e678effe3822ba2d5042552468f90d8187f4107295a49`. `tests/test_report_stamp.py` échoue si le code du dépôt ne correspond plus à cette empreinte.
