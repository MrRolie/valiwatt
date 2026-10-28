# Validation et surveillance d'une prévision de la demande d'électricité au Québec, v3 : la prévision de midi

Rapport généré à partir des exécutions MLflow de v3 (les exécutions de référence que nomme le registre du gel `docs/freeze-v3.json`, ajouté au commit `cc93edeba8fa`), code d'empreinte `2d4e6e33ee98`, règles d'empreinte `526e7ee3484c`. Chaque valeur mesurée vient de ces exécutions et porte son étiquette d'évaluation (section 8.4 du plan) : « dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide » pour 2020–2024, « vu par les développeurs » pour la répétition de 2024, et pour 2025 « données réservées » seulement quand son test de référence est propre ; une sensibilité n'est jamais jugée. « Les développeurs » sont les agents IA qui ont construit ce projet et le propriétaire qui les dirige. Les constantes de conception que ce rapport écrit en chiffres sont lues du code qui les applique. Le rapport de v2 (`docs/report/`) reste tel que rendu ; ce rapport ne le change pas. La version anglaise est dérivée de celle-ci.

> Données de demande : Hydro-Québec, « Historique de la demande d'électricité au Québec » (https://donnees.hydroquebec.com/explore/dataset/historique-demande-electricite-quebec/), sous licence CC BY-NC 4.0 (https://creativecommons.org/licenses/by-nc/4.0/). Hydro-Québec offre ces données à titre informatif et sans garantie de qualité ; ce ne sont pas des données officielles. Ce projet les a contrôlées, en a masqué ou corrigé quelques heures (chaque cas est documenté) et les a agrégées ; il ne redistribue pas le jeu de données, et les rapports n'en citent que quelques valeurs horaires, comme preuves.
>
> Données météorologiques : Environnement et Changement climatique Canada, utilisées sous la Licence du gouvernement ouvert – Canada (https://ouvert.canada.ca/fr/licence-du-gouvernement-ouvert-canada).
>
> Données extraites du site Web « Données climatiques historiques » d’Environnement et Changement climatique Canada ( https://climat.meteo.gc.ca/index_f.html ) le 2026-10-14.
>
> Prévisions météorologiques : guidance NBS du National Blend of Models de la NOAA pour la station CYUL (Montréal-Trudeau), téléchargée des archives de l'Iowa Environmental Mesonet de l'Université d'État de l'Iowa (https://mesonet.agron.iastate.edu/). Ce projet a interpolé à l'heure ces prévisions trihoraires et les a converties en degrés Celsius : les valeurs qu'il utilise sont modifiées et ne sont pas des données originales de la NOAA.
>
> Ni Hydro-Québec, ni Environnement et Changement climatique Canada, ni la NOAA, ni l'Iowa Environmental Mesonet n'ont produit, revu ou approuvé ce travail, et ce projet n'est affilié ni à la NOAA ni à l'Iowa Environmental Mesonet.
>
> Construit avec Claude Code sous la direction du propriétaire du dépôt.

## En bref

- **Statut : candidat.** Choisi sur 2020–2024 (dans l'échantillon du choix), montré sur 2024 vue par les développeurs ; le test sur données réservées, 2025, est en attente.
- **La prévision du jour J est émise à midi la veille** (12 h HNE le jour J−1, 13 h HAE en heure avancée ; soit 17 h UTC), et non plus à la fin de la veille comme celle de v2. Pourquoi midi : selon le tarif Flex D d'Hydro-Québec, un tarif résidentiel public (Tarifs d'électricité 2026, édition en vigueur le 2026-04-01, section 9), l'avis d'un événement de pointe est envoyé avant 15 h la veille (art. 2.71) ; une prévision qu'on compare à cet avis doit être émise avant lui, et celle de midi l'est 3 heures avant, 2 en heure avancée. Le texte du tarif est cité, non reproduit. Ce rapport ne dit jamais comment un événement de pointe est déclenché, seulement comment la prévision de midi se comporte dans les heures de pointe du tarif et comment elle classe ses fenêtres.
- Validation sur 2020–2024 (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) : l'erreur absolue moyenne en pourcentage (EAMP) est de 3,25 % pour v3, 3,28 % pour la forme de v2 à midi, 4,11 % pour la forme de v1 à midi et 2,77 % pour la forme de v2 à minuit, chacune sur ses heures. Verdict des termes : « meilleur » ; verdict de la famille : « meilleur ». Ces années ont suggéré les termes, les classes d'intervalles et le sous-ensemble qui décide : ces verdicts ne soutiennent aucune affirmation (section 2).
- Répétition de 2024 (_vu par les développeurs_) : EAMP de 2,95 %, couverture de 92,1 % ; verdict des termes « meilleur », de la famille « meilleur ». Une année déjà vue ne soutient aucune affirmation (section 3).
- 2025 : en attente ; le test de référence sera le premier rejeu de 2025 commencé sous `v3-rules-frozen`, après le test de référence de v2 (section 4).

## Fiche du modèle

| | |
|---|---|
| **Statut** | **Statut : candidat.** Choisi sur 2020–2024 (dans l'échantillon du choix), montré sur 2024 vue par les développeurs ; le test sur données réservées, 2025, est en attente. |
| **Ce qu'il prévoit** | La demande horaire d'électricité du Québec publiée par Hydro-Québec, pour les 24 heures du jour J, émise à midi la veille (section 1), avec un intervalle de prévision dont la demi-largeur dépend de la température prévue. |
| **Usage prévu** | Démontrer, sur une prévision réelle, une validation et une surveillance du type de celles d'une équipe de gestion du risque de modèle. |
| **Hors champ** | Toute répartition opérationnelle de la production, tout négoce ou tarification, toute décision, et tout déclenchement d'un événement de pointe. Hydro-Québec publie ces données à titre informatif ; ce ne sont pas des données officielles. |
| **Modèle** | Le modèle additif de v2, ajusté de la même façon, avec la demande 48 h et 168 h avant l'heure visée et trois termes de plus : le froid moyen du jour, le froid moyen de l'avant-veille et le vent par le froid (section 1). Le modèle figé pour 2025 compte 341 colonnes, ajusté sur les données jusqu'au 2024-12-24 ; empreinte `5c64cc9af273`. Comparé à la forme de v2 à midi, à la forme de v1 à midi et à la forme de v2 à minuit, qui ne recommandent jamais rien. |
| **Intervalle** (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) | Modèle figé pour 2025 : sous −11,1 °C, prévision × (1 ± 8,12 %) ; ailleurs, prévision × (1 ± 6,38 %) (section 2.4). |
| **Validation** (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) | EAMP de v3 de 3,25 % [3,11 % ; 3,41 %] ; verdicts : termes « meilleur », famille « meilleur » (section 2). |
| **Répétition de 2024** (_vu par les développeurs_) | EAMP de 2,95 %, couverture de 92,1 % (section 3). |
| **2025** | En attente (section 4). |
| **Mise en garde météo** | Une prévision météo du NBM (une exécution) est disponible, par hypothèse, 2 heures après son heure d'émission, et une heure observée d'ECCC dès son propre libellé ; tout résultat qui lit la prévision ou la moyenne de l'avant-veille repose sur ces hypothèses (section 1). La température observée de l'heure visée n'est qu'une borne optimiste. |
| **Limites** | Section 6. |
| **Auteur** | Construit avec Claude Code sous la direction du propriétaire du dépôt. |

## 1. La prévision de midi, et ce que v3 change

- **Trois horloges.** La prévision du jour J (en heure normale) est émise à son origine, le jour J−1 à 17 h UTC, soit 12 h HNE, ou 13 h HAE en heure avancée. Les heures de J ne bougent pas : chacune porte l'heure UTC de sa fin, du jour J à 6 h UTC au lendemain à 5 h UTC. J est noté une heure après sa dernière heure, comme dans v2. La forme de v2 à minuit garde l'origine de v2, le jour J à 5 h UTC.
- **Les entrées, connues à l'origine.** La demande 48 h et 168 h avant l'heure visée (celle de 24 h avant ne l'est pas pour la moitié de J) ; l'heure locale et le type de jour ; la température et le vent prévus par le NBM à l'heure visée, par l'exécution qui alimente J ; la moyenne des températures prévues des 24 heures de J ; la moyenne des températures observées par ECCC à la station principale sur les 24 heures de l'avant-veille (J−2), qui n'existe qu'avec au moins 20 heures utilisables : sinon elle manque, et rien n'est imputé.
- **L'hypothèse de disponibilité.** Une prévision météo du NBM (une exécution) est réputée disponible 2 heures après son heure d'émission, et une heure d'ECCC dès son propre libellé, avec sa valeur telle que téléchargée : ni le délai de publication ni le contrôle de qualité ultérieur ne sont modélisés. Une variante de validation rend chaque exécution disponible 6 heures après son émission (section 2.6).
- **Les trois termes**, tous linéaires, chacun de base 18 °C : le froid moyen du jour, max(0, 18 − la moyenne prévue des heures de J) ; le froid moyen de l'avant-veille, max(0, 18 − la moyenne observée de J−2), qui permet au modèle de retirer de la demande 48 h plus tôt la part qui tenait à la météo de J−2 ; le vent par le froid, le vent prévu en km/h (nœuds × 1,852) multiplié par max(0, 18 − la température prévue de l'heure), nul par temps doux. Le vent prévu doit rester entre 0 et 100 nœuds, bornes comprises. Aucun terme ne lit la demande.
- **Les modèles comparés**, qui ne recommandent jamais rien : la forme de v2 à midi (v2 exactement, sur les mêmes lignes que v3, sans les termes) ; la forme de v1 à midi (les moindres carrés de v1 sur la même prévision) ; la forme de v2 à minuit (v2 avec toutes ses horloges et entrées, pour mesurer ce que coûte une émission douze heures plus tôt).
- **L'erreur.** L'erreur absolue moyenne en pourcentage (EAMP) dit de combien, en moyenne, la prévision s'écarte de la demande réelle, en pourcentage de celle-ci. Le biais signé dit dans quel sens : biais = (demande − prévision) / demande, positif quand la prévision est trop basse.
- **L'intervalle, et sa couverture.** Chaque prévision porte un intervalle, prévision × (1 ± q). La demi-largeur q est le quantile de rang ⌈(n + 1) × 0,90⌉ des n erreurs relatives |demande − prévision| / prévision des 12 mois précédents déjà notés : la forme d'un intervalle conforme « split », sans sa garantie, car ces erreurs horaires sont autocorrélées. La couverture est la part des heures dont la demande tombe dans l'intervalle ; sa cible est 90 %. Elle n'est mesurée qu'à partir du premier mois précédé de 12 mois notés, sur moins d'heures que l'erreur.
- **Les intervalles par température.** Ce quantile est pris à part pour les heures froides, sous le seuil de chaque ajustement (le 5ᵉ centile des températures prévues de ses propres lignes d'entraînement), et pour les autres. Une classe froide de moins de 120 erreurs, ou d'erreurs de moins de 4 semaines du lundi au dimanche, prend la demi-largeur commune de toutes les erreurs, et le modèle le consigne.
- **Le facteur d'inflation de la variance** d'un coefficient dit de combien sa variance grandit parce que sa colonne recoupe les autres colonnes du modèle : plus il est grand, moins le coefficient se lit seul.
- **La règle de décision.** Sur les heures communes (la demande et les trois prévisions de midi), la différence appariée d'erreur absolue en pourcentage, heure par heure, avec un intervalle à 95 % par blocs de semaines (1 000 rééchantillonnages dans cette validation ; chaque rejeu donne le sien, sections 3 et 4) : **termes**, v3 moins la forme de v2 à midi ; **famille**, la forme de v2 à midi moins celle de v1 à midi ; leur somme, le changement entier, rapporté et jamais jugé ; et l'**horizon**, la forme de v2 à midi moins celle à minuit, rapporté et jamais jugé. Une différence négative est une erreur plus basse : elle est meilleure quand la borne haute de son intervalle est sous zéro, pire quand la borne basse est au-dessus, non séparée sinon. Les sous-ensembles : toutes les heures communes ; la queue froide, celles dont la température prévue (prévision de midi) est sous le 5ᵉ centile des années de conception ; les fenêtres d'événements, celles des heures de pointe du tarif : du 1er décembre au 31 mars, sauf les 24, 25, 26 et 31 décembre, les 1er et 2 janvier, le Vendredi saint et le lundi de Pâques (art. 2.67), les heures se terminant à 7, 8, 9 et 10 et à 17, 18, 19 et 20 heures, heure locale. Un sous-ensemble est non testable sous 120 heures ou 4 semaines. **Le verdict des termes** : meilleur quand le contraste des fenêtres est meilleur, celui de toutes les heures pas pire et la queue froide testable et pas pire ; « meilleur dans les fenêtres d'événements, la queue froide non testable » si elle ne l'est pas ; pas montré meilleur quand le contraste des fenêtres est non séparé, ou meilleur mais payé ailleurs ; pire quand il est pire ; non testable quand les fenêtres le sont. **Le verdict de la famille** suit la règle de v2 : toutes les heures communes décident et la queue froide sert de garde-fou ; meilleur quand le contraste de toutes les heures est meilleur et que la queue froide, testable, n'est pas pire ; « meilleur en moyenne, la queue froide non testable » quand elle ne l'est pas ; pas montré meilleur quand ce contraste est non séparé, ou meilleur avec une queue froide pire ; pire quand il est pire. « Les termes améliorent la prévision de midi dans les fenêtres d'événements de pointe » ne se dit que du verdict des termes, « le modèle additif mérite sa place face à la forme de v1 à midi » que de celui de la famille, et seulement sur données réservées.
- **Le classement des fenêtres.** Dans chaque période notée, chaque partie d'hiver est classée seule : ses w fenêtres admissibles (les quatre heures avec une demande et les trois prévisions de midi) sont rangées par demande moyenne réelle et par la moyenne prévue de chaque modèle de midi ; k = plancher(30 × w / W + 0,5), où 30 est le plus grand nombre d'événements d'un hiver (120 heures en événements de 4 heures, art. 2.70) et W le nombre de fenêtres de tout l'hiver, deux par jour hors jours exclus ; pour chaque modèle, combien de ses k plus hautes sont parmi les k plus hautes réelles. La forme de v2 à minuit n'est pas classée : elle est émise après l'avis.

## 2. Validation sur 2020–2024 (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_)

Chaque mois de 2020-01 à 2024-12 (60 plis) est prévu par des modèles réajustés sur les heures qui le précèdent, moins les 7 derniers jours, chaque modèle de midi à l'origine de midi de son pli. Ces années ont suggéré les termes, les classes d'intervalles et les fenêtres d'événements comme sous-ensemble qui décide : chaque résultat est dans l'échantillon de ces choix, et seul 2025 peut les tester.

### 2.1 Les contrastes

Les sous-ensembles (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) : 43 791 heures communes sur 262 semaines du lundi au dimanche ; la queue froide, les heures dont la température prévue (prévision de midi) est sous −11,1 °C, 1 834 heures sur 46 semaines, testable ; les fenêtres d'événements, 4 608 heures sur 92 semaines, testables.

| Contraste (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) | Sous-ensemble | Heures | Différence d'EAMP | IC 95 % | État |
|---|---|---|---|---|---|
| termes : v3 moins la forme de v2 à midi | toutes les heures communes | 43 791 | −0,03 pt | [−0,06 pt ; 0,00 pt] | non séparé |
| termes : v3 moins la forme de v2 à midi | la queue froide | 1 834 | 0,00 pt | [−0,14 pt ; +0,15 pt] | non séparé |
| termes : v3 moins la forme de v2 à midi | les fenêtres d'événements | 4 608 | −0,12 pt | [−0,19 pt ; −0,06 pt] | meilleur |
| famille : la forme de v2 à midi moins la forme de v1 à midi | toutes les heures communes | 43 791 | −0,83 pt | [−0,95 pt ; −0,72 pt] | meilleur |
| famille : la forme de v2 à midi moins la forme de v1 à midi | la queue froide | 1 834 | −0,62 pt | [−0,96 pt ; −0,26 pt] | meilleur |
| famille : la forme de v2 à midi moins la forme de v1 à midi | les fenêtres d'événements | 4 608 | −0,10 pt | [−0,18 pt ; −0,01 pt] | meilleur |
| changement entier à midi : v3 moins la forme de v1 à midi (rapporté, jamais jugé) | toutes les heures communes | 43 791 | −0,86 pt | [−0,97 pt ; −0,75 pt] | meilleur |
| changement entier à midi : v3 moins la forme de v1 à midi (rapporté, jamais jugé) | la queue froide | 1 834 | −0,62 pt | [−0,97 pt ; −0,29 pt] | meilleur |
| changement entier à midi : v3 moins la forme de v1 à midi (rapporté, jamais jugé) | les fenêtres d'événements | 4 608 | −0,22 pt | [−0,33 pt ; −0,11 pt] | meilleur |

Sur chaque sous-ensemble, les termes plus la famille donnent le changement entier, au calcul près.

| Horizon : la forme de v2 à midi moins la forme de v2 à minuit, rapporté, jamais jugé (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) | Heures | Différence d'EAMP | IC 95 % |
|---|---|---|---|
| les heures propres à l'horizon (la demande, la forme de v2 à midi et celle à minuit) | 43 779 | +0,51 pt | [+0,46 pt ; +0,57 pt] |
| la queue froide | 1 834 | +0,57 pt | [+0,21 pt ; +0,89 pt] |
| les fenêtres d'événements | 4 608 | +0,74 pt | [+0,61 pt ; +0,88 pt] |

### 2.2 Les verdicts

- **Verdict des termes** (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) : « meilleur ».
- **Verdict de la famille** (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) : « meilleur ».
- Sur des années qui ont suggéré les choix, ni l'un ni l'autre ne soutient d'affirmation ; chacun ne se dit que de son propre contraste.

### 2.3 L'erreur de chaque modèle

| Modèle (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) | EAMP | IC 95 % | Biais signé | Heures |
|---|---|---|---|---|
| v3 (production) | 3,25 % | [3,11 % ; 3,41 %] | +1,44 % | 43 791 |
| la forme de v2 à midi | 3,28 % | [3,13 % ; 3,43 %] | +1,46 % | 43 791 |
| la forme de v1 à midi | 4,11 % | [3,98 % ; 4,24 %] | +1,47 % | 43 791 |
| la forme de v2 à minuit | 2,77 % | [2,66 % ; 2,89 %] | +1,21 % | 43 814 |
| la même heure une semaine plus tôt (référence naïve) | 7,84 % | [7,27 % ; 8,45 %] | −0,56 % | 43 826 |

### 2.4 Les intervalles par température

| Classe de v3 (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) | Couverture (nominale : 90 %) | IC 95 % | Heures |
|---|---|---|---|
| froide | 74,4 % | [61,7 % ; 88,9 %] | 1 189 |
| le reste | 90,6 % | [88,8 % ; 92,0 %] | 33 824 |
| toutes | 90,0 % | [88,3 % ; 91,7 %] | 35 013 |
| une seule demi-largeur, comme v2 (sensibilité) | 90,6 % | [88,9 % ; 92,1 %] | 35 013 |

La couverture n'est jamais un critère de la règle de décision. Chaque pli calibré, son seuil et ses demi-largeurs :

| Pli de v3 (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) | Seuil | Froide | Le reste | Commune | Erreurs froides | Semaines froides | Repli |
|---|---|---|---|---|---|---|---|
| 2021-01 | −12,4 °C | 8,77 % | 7,46 % | 7,50 % | 244 | 9 | non |
| 2021-02 | −12,4 °C | 8,05 % | 7,29 % | 7,31 % | 232 | 7 | non |
| 2021-03 | −13,0 °C | 4,13 % | 7,13 % | 7,10 % | 175 | 6 | non |
| 2021-04 | −12,8 °C | 4,13 % | 7,16 % | 7,14 % | 209 | 9 | non |
| 2021-05 | −12,8 °C | 4,13 % | 7,00 % | 6,96 % | 209 | 9 | non |
| 2021-06 | −12,8 °C | 4,13 % | 6,97 % | 6,94 % | 209 | 9 | non |
| 2021-07 | −12,6 °C | 4,95 % | 7,15 % | 7,14 % | 243 | 10 | non |
| 2021-08 | −12,4 °C | 6,40 % | 7,21 % | 7,19 % | 260 | 10 | non |
| 2021-09 | −12,2 °C | 6,75 % | 7,25 % | 7,23 % | 273 | 10 | non |
| 2021-10 | −12,2 °C | 6,75 % | 7,24 % | 7,23 % | 273 | 10 | non |
| 2021-11 | −12,0 °C | 6,85 % | 7,04 % | 7,03 % | 319 | 10 | non |
| 2021-12 | −11,9 °C | 6,93 % | 6,72 % | 6,72 % | 327 | 10 | non |
| 2022-01 | −11,7 °C | 3,65 % | 7,00 % | 6,91 % | 309 | 11 | non |
| 2022-02 | −12,6 °C | 8,81 % | 7,27 % | 7,42 % | 508 | 11 | non |
| 2022-03 | −13,3 °C | 8,50 % | 7,46 % | 7,59 % | 546 | 11 | non |
| 2022-04 | −13,5 °C | 8,62 % | 7,42 % | 7,55 % | 555 | 9 | non |
| 2022-05 | −13,3 °C | 8,57 % | 7,56 % | 7,68 % | 566 | 9 | non |
| 2022-06 | −13,3 °C | 8,57 % | 7,59 % | 7,73 % | 566 | 9 | non |
| 2022-07 | −13,3 °C | 8,57 % | 7,45 % | 7,56 % | 566 | 9 | non |
| 2022-08 | −13,0 °C | 8,62 % | 7,38 % | 7,54 % | 608 | 9 | non |
| 2022-09 | −12,8 °C | 8,57 % | 7,34 % | 7,51 % | 621 | 9 | non |
| 2022-10 | −12,8 °C | 8,57 % | 7,38 % | 7,56 % | 621 | 9 | non |
| 2022-11 | −12,8 °C | 8,57 % | 7,44 % | 7,61 % | 621 | 9 | non |
| 2022-12 | −12,6 °C | 8,57 % | 7,61 % | 7,76 % | 644 | 9 | non |
| 2023-01 | −12,6 °C | 8,57 % | 7,13 % | 7,31 % | 644 | 9 | non |
| 2023-02 | −12,4 °C | 7,25 % | 7,21 % | 7,21 % | 263 | 8 | non |
| 2023-03 | −12,6 °C | 10,58 % | 7,34 % | 7,42 % | 174 | 9 | non |
| 2023-04 | −12,6 °C | 11,36 % | 7,14 % | 7,21 % | 147 | 8 | non |
| 2023-05 | −12,6 °C | 11,36 % | 7,19 % | 7,28 % | 147 | 8 | non |
| 2023-06 | −12,4 °C | 10,87 % | 7,16 % | 7,23 % | 150 | 8 | non |
| 2023-07 | −12,2 °C | 10,87 % | 7,14 % | 7,21 % | 150 | 8 | non |
| 2023-08 | −12,2 °C | 10,87 % | 7,13 % | 7,20 % | 150 | 8 | non |
| 2023-09 | −12,2 °C | 10,87 % | 7,08 % | 7,16 % | 150 | 8 | non |
| 2023-10 | −12,0 °C | 10,87 % | 7,04 % | 7,14 % | 164 | 8 | non |
| 2023-11 | −11,9 °C | 10,87 % | 7,02 % | 7,13 % | 168 | 8 | non |
| 2023-12 | −11,9 °C | 10,87 % | 6,94 % | 7,04 % | 168 | 8 | non |
| 2024-01 | −11,7 °C | 10,58 % | 7,05 % | 7,13 % | 187 | 9 | non |
| 2024-02 | −11,9 °C | 10,57 % | 6,88 % | 6,99 % | 191 | 7 | non |
| 2024-03 | −11,7 °C | 6,66 % | 6,69 % | 6,66 % | 87 | 4 | oui |
| 2024-04 | −11,7 °C | 6,77 % | 6,79 % | 6,77 % | 86 | 3 | oui |
| 2024-05 | −11,7 °C | 6,47 % | 6,49 % | 6,47 % | 86 | 3 | oui |
| 2024-06 | −11,7 °C | 6,45 % | 6,47 % | 6,45 % | 86 | 3 | oui |
| 2024-07 | −11,5 °C | 6,49 % | 6,49 % | 6,49 % | 111 | 6 | oui |
| 2024-08 | −11,5 °C | 6,51 % | 6,52 % | 6,51 % | 111 | 6 | oui |
| 2024-09 | −11,3 °C | 6,88 % | 6,55 % | 6,55 % | 122 | 6 | non |
| 2024-10 | −11,1 °C | 6,88 % | 6,50 % | 6,50 % | 134 | 6 | non |
| 2024-11 | −11,1 °C | 6,88 % | 6,48 % | 6,49 % | 134 | 6 | non |
| 2024-12 | −11,1 °C | 6,88 % | 6,28 % | 6,29 % | 134 | 6 | non |

- Modèle figé pour 2024 (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) : seuil −11,7 °C ; demi-largeurs froide 10,58 %, du reste 7,05 %, commune 7,13 % ; 187 erreurs froides sur 9 semaines ; repli : non.
- Modèle figé pour 2025 (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) : seuil −11,1 °C ; demi-largeurs froide 8,12 %, du reste 6,38 %, commune 6,40 % ; 181 erreurs froides sur 7 semaines ; repli : non.

### 2.5 Les termes : coefficients et facteurs d'inflation de la variance

| Modèle figé pour 2025 (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) | Coefficient | Unité | Facteur d'inflation de la variance |
|---|---|---|---|
| froid moyen du jour | 85,962 | MW par °C de froid (sous 18 °C) | 51,20 |
| froid moyen de l'avant-veille | −12,991 | MW par °C de froid (sous 18 °C) | 14,83 |
| vent par le froid | 1,938 | MW par km/h et par °C de froid (sous 18 °C) | 3,33 |
| demande 48 h avant | 0,242 | MW par MW | 14,66 |
| demande 168 h avant | 0,080 | MW par MW | 5,20 |

| Modèle figé pour 2024 (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) | Coefficient | Unité | Facteur d'inflation de la variance |
|---|---|---|---|
| froid moyen du jour | 93,813 | MW par °C de froid (sous 18 °C) | 50,97 |
| froid moyen de l'avant-veille | −8,141 | MW par °C de froid (sous 18 °C) | 15,19 |
| vent par le froid | 1,867 | MW par km/h et par °C de froid (sous 18 °C) | 3,27 |
| demande 48 h avant | 0,232 | MW par MW | 15,09 |
| demande 168 h avant | 0,077 | MW par MW | 5,34 |

Chaque coefficient est imprimé par unité de sa colonne, sans normalisation, à côté de son facteur d'inflation de la variance contre les autres colonnes du modèle. Un signe est descriptif et n'est la preuve d'aucun mécanisme : le froid moyen du jour est proche des températures de chaque heure, et le coefficient du froid moyen de l'avant-veille est le net de deux effets, la correction de la demande 48 h plus tôt et le froid qui persiste depuis J−2. La preuve de chaque terme est son contraste « laissé de côté » (section 2.6).

| Entrée de v3, hors de la plage d'entraînement de son pli (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) | Heures prévues | Sous la plage | Au-dessus | EAMP hors plage |
|---|---|---|---|---|
| la température prévue | 43 802 | 92 | 12 | 5,00 % |
| froid moyen du jour | 43 802 | 0 | 120 | 4,66 % |
| froid moyen de l'avant-veille | 43 802 | 0 | 120 | 6,48 % |
| vent par le froid | 43 802 | 0 | 31 | 4,75 % |

### 2.6 Bornes et sensibilités (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_ ; _sensibilité, jamais jugée_)

Chacune est la différence appariée d'EAMP de la variante moins v3, sur les heures communes où la variante prévoit, et sur chaque sous-ensemble ainsi réduit ; jamais jugée. Le sens est l'inverse de celui des contrastes : une différence positive dit que la variante fait pire que v3, donc que ce qu'elle retire ou change aidait v3 ; une différence négative, qu'elle fait mieux.

| Variante (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_ ; _sensibilité, jamais jugée_) | Sous-ensemble | Heures | Différence d'EAMP | IC 95 % |
|---|---|---|---|---|
| v3 sans le froid moyen du jour | toutes les heures communes | 43 791 | −0,02 pt | [−0,03 pt ; 0,00 pt] |
| v3 sans le froid moyen du jour | la queue froide | 1 834 | +0,01 pt | [−0,07 pt ; +0,08 pt] |
| v3 sans le froid moyen du jour | les fenêtres d'événements | 4 608 | +0,04 pt | [+0,01 pt ; +0,07 pt] |
| v3 sans le froid moyen de l'avant-veille | toutes les heures communes | 43 791 | −0,01 pt | [−0,02 pt ; 0,00 pt] |
| v3 sans le froid moyen de l'avant-veille | la queue froide | 1 834 | 0,00 pt | [−0,01 pt ; +0,02 pt] |
| v3 sans le froid moyen de l'avant-veille | les fenêtres d'événements | 4 608 | 0,00 pt | [−0,01 pt ; +0,02 pt] |
| v3 sans le vent par le froid | toutes les heures communes | 43 791 | +0,04 pt | [+0,02 pt ; +0,06 pt] |
| v3 sans le vent par le froid | la queue froide | 1 834 | −0,02 pt | [−0,15 pt ; +0,12 pt] |
| v3 sans le vent par le froid | les fenêtres d'événements | 4 608 | +0,08 pt | [+0,03 pt ; +0,12 pt] |
| v3, la marge de disponibilité plus longue | toutes les heures communes | 43 767 | +0,01 pt | [0,00 pt ; +0,02 pt] |
| v3, la marge de disponibilité plus longue | la queue froide | 1 825 | +0,07 pt | [+0,01 pt ; +0,13 pt] |
| v3, la marge de disponibilité plus longue | les fenêtres d'événements | 4 600 | +0,02 pt | [0,00 pt ; +0,04 pt] |
| v3 sur la température observée (borne optimiste) | toutes les heures communes | 43 727 | −0,17 pt | [−0,23 pt ; −0,10 pt] |
| v3 sur la température observée (borne optimiste) | la queue froide | 1 833 | −0,28 pt | [−0,56 pt ; −0,02 pt] |
| v3 sur la température observée (borne optimiste) | les fenêtres d'événements | 4 608 | −0,52 pt | [−0,67 pt ; −0,38 pt] |
| la même heure une semaine plus tôt (référence naïve) | toutes les heures communes | 43 791 | +4,59 pt | [+4,07 pt ; +5,15 pt] |
| la même heure une semaine plus tôt (référence naïve) | la queue froide | 1 834 | +7,79 pt | [+6,29 pt ; +9,63 pt] |
| la même heure une semaine plus tôt (référence naïve) | les fenêtres d'événements | 4 608 | +6,81 pt | [+6,02 pt ; +7,62 pt] |

- v3, la marge de disponibilité plus longue (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_ ; _sensibilité, jamais jugée_) : heures communes sans sa prévision, par raison : aucune des prévisions disponibles ne couvre le jour (24 heures).
- v3 sur la température observée (borne optimiste) (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_ ; _sensibilité, jamais jugée_) : heures communes sans sa prévision, par raison : la moyenne observée du jour manque (5 heures) ; la température observée manque (40 heures) ; la température observée manque et la moyenne observée du jour manque (19 heures).

### 2.7 Les heures les plus chargées (rapportées, jamais jugées)

| Heure de fin, UTC (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) | Demande | Température prévue | v3 (production) | la forme de v2 à midi | la forme de v1 à midi | la forme de v2 à minuit |
|---|---|---|---|---|---|---|
| 2023-02-03 23:00 UTC | 42 473 MW | −24,8 °C | +0,3 % | +4,3 % | +9,6 % | +4,4 % |
| 2023-02-04 00:00 UTC | 42 254 MW | −25,0 °C | +2,2 % | +6,0 % | +9,3 % | +6,5 % |
| 2023-02-04 01:00 UTC | 41 799 MW | −25,2 °C | +2,3 % | +6,1 % | +9,0 % | +8,2 % |
| 2023-02-04 02:00 UTC | 41 141 MW | −25,4 °C | +2,1 % | +5,9 % | +8,7 % | +9,3 % |
| 2023-02-03 22:00 UTC | 40 585 MW | −24,6 °C | −2,9 % | +1,7 % | +8,0 % | +0,4 % |


L'erreur signée de chaque modèle est (demande − prévision) / demande (section 1).

### 2.8 Le classement des fenêtres (rapporté, jamais jugé)

| 2020, hiver (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) | Jours | W | w | k | v3 (production) | la forme de v2 à midi | la forme de v1 à midi | Fenêtres non admissibles |
|---|---|---|---|---|---|---|---|---|
| 2019-20 | 2020-01-01 au 2020-03-31 | 232 | 178 | 23 | 18 sur 23 | 19 sur 23 | 18 sur 23 | 0 |
| 2020-21 | 2020-12-01 au 2020-12-31 | 230 | 54 | 7 | 7 sur 7 | 7 sur 7 | 7 sur 7 | 0 |


| 2021, hiver (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) | Jours | W | w | k | v3 (production) | la forme de v2 à midi | la forme de v1 à midi | Fenêtres non admissibles |
|---|---|---|---|---|---|---|---|---|
| 2020-21 | 2021-01-01 au 2021-03-31 | 230 | 176 | 23 | 16 sur 23 | 17 sur 23 | 17 sur 23 | 0 |
| 2021-22 | 2021-12-01 au 2021-12-31 | 230 | 54 | 7 | 3 sur 7 | 2 sur 7 | 2 sur 7 | 0 |


| 2022, hiver (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) | Jours | W | w | k | v3 (production) | la forme de v2 à midi | la forme de v1 à midi | Fenêtres non admissibles |
|---|---|---|---|---|---|---|---|---|
| 2021-22 | 2022-01-01 au 2022-03-31 | 230 | 176 | 23 | 15 sur 23 | 15 sur 23 | 16 sur 23 | 0 |
| 2022-23 | 2022-12-01 au 2022-12-31 | 230 | 54 | 7 | 5 sur 7 | 5 sur 7 | 5 sur 7 | 0 |


| 2023, hiver (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) | Jours | W | w | k | v3 (production) | la forme de v2 à midi | la forme de v1 à midi | Fenêtres non admissibles |
|---|---|---|---|---|---|---|---|---|
| 2022-23 | 2023-01-01 au 2023-03-31 | 230 | 176 | 23 | 16 sur 23 | 16 sur 23 | 16 sur 23 | 0 |
| 2023-24 | 2023-12-01 au 2023-12-31 | 230 | 54 | 7 | 5 sur 7 | 6 sur 7 | 5 sur 7 | 0 |


| 2024, hiver (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) | Jours | W | w | k | v3 (production) | la forme de v2 à midi | la forme de v1 à midi | Fenêtres non admissibles |
|---|---|---|---|---|---|---|---|---|
| 2023-24 | 2024-01-01 au 2024-03-31 | 230 | 176 | 23 | 15 sur 23 | 15 sur 23 | 15 sur 23 | 0 |
| 2024-25 | 2024-12-01 au 2024-12-31 | 230 | 54 | 7 | 5 sur 7 | 5 sur 7 | 5 sur 7 | 0 |


Chaque case de modèle dit combien de ses k fenêtres les plus hautes sont parmi les k plus hautes réelles.

### 2.9 L'entrée de midi

| Jours de conception (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) | Nombre |
|---|---|
| jours | 2 192 |
| jours alimentés par une prévision | 2 191 |
| jours sans prévision couvrante | 1 |
| jours sans moyenne de l'avant-veille | 3 |

- 2019-01-01 (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) : pas de prévision, aucune prévision disponible avant l’origine.
- Jours sans moyenne de l'avant-veille (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) : du 2019-01-01 au 2019-01-02 et le 2022-04-24 ; aucun modèle de midi ne les prévoit, et leurs heures sont écartées pour la raison « la moyenne de l'avant-veille manque ».

### 2.10 La vérification de reproduction

La forme de v2 à minuit, dans le harnais de v3, reproduit les prévisions de validation consignées de v2 sur 35 045 heures, du 2020-01-01 au 2023-12-31 (_dans l'échantillon du choix des termes, des classes d'intervalles et du sous-ensemble qui décide_) : aucun écart, pour une tolérance de 1 × 10⁻⁶ MW.

## 3. La répétition de 2024 (_vu par les développeurs_)

Les modèles figés pour 2024, ajustés sur les données jusqu'au 2023-12-24, rejouent 2024 heure par heure à midi (exécution `fe5d0e5bf99f411fa3c814f6ff51628c`). 2024 est une année de validation : chaque nombre est vu par les développeurs et ne soutient aucune affirmation. La répétition a vérifié qu'elle lit exactement l'entrée de la validation : 8 784 heures et 366 jours, 0 différence. Ses intervalles par blocs de semaines viennent de 1 000 rééchantillonnages.

| Modèle (_vu par les développeurs_) | EAMP | IC 95 % | Couverture |
|---|---|---|---|
| v3 (production) | 2,95 % | [2,67 % ; 3,24 %] | 92,1 % |
| la forme de v2 à midi | 3,01 % | [2,70 % ; 3,33 %] | 92,2 % |
| la forme de v1 à midi | 3,84 % | [3,61 % ; 4,10 %] | 91,1 % |
| la forme de v2 à minuit | 2,58 % | [2,33 % ; 2,85 %] | aucun intervalle |


| Classe de v3 (_vu par les développeurs_) | Couverture (nominale : 90 %) | IC 95 % | Heures |
|---|---|---|---|
| froide | 100,0 % | [100,0 % ; 100,0 %] | 129 |
| le reste | 92,0 % | [89,2 % ; 94,4 %] | 8 649 |
| toutes | 92,1 % | [89,4 % ; 94,4 %] | 8 778 |


Les sous-ensembles (_vu par les développeurs_) : 8 778 heures communes sur 53 semaines du lundi au dimanche ; la queue froide, les heures dont la température prévue (prévision de midi) est sous −11,1 °C, 181 heures sur 7 semaines, testable ; les fenêtres d'événements, 920 heures sur 19 semaines, testables.

| Contraste (_vu par les développeurs_) | Sous-ensemble | Heures | Différence d'EAMP | IC 95 % | État |
|---|---|---|---|---|---|
| termes : v3 moins la forme de v2 à midi | toutes les heures communes | 8 778 | −0,06 pt | [−0,12 pt ; +0,01 pt] | non séparé |
| termes : v3 moins la forme de v2 à midi | la queue froide | 181 | −0,02 pt | [−0,54 pt ; +0,81 pt] | non séparé |
| termes : v3 moins la forme de v2 à midi | les fenêtres d'événements | 920 | −0,15 pt | [−0,27 pt ; −0,02 pt] | meilleur |
| famille : la forme de v2 à midi moins la forme de v1 à midi | toutes les heures communes | 8 778 | −0,84 pt | [−1,10 pt ; −0,59 pt] | meilleur |
| famille : la forme de v2 à midi moins la forme de v1 à midi | la queue froide | 181 | −0,80 pt | [−1,35 pt ; −0,54 pt] | meilleur |
| famille : la forme de v2 à midi moins la forme de v1 à midi | les fenêtres d'événements | 920 | −0,31 pt | [−0,49 pt ; −0,10 pt] | meilleur |
| changement entier à midi : v3 moins la forme de v1 à midi (rapporté, jamais jugé) | toutes les heures communes | 8 778 | −0,90 pt | [−1,14 pt ; −0,66 pt] | meilleur |
| changement entier à midi : v3 moins la forme de v1 à midi (rapporté, jamais jugé) | la queue froide | 181 | −0,82 pt | [−1,24 pt ; −0,08 pt] | meilleur |
| changement entier à midi : v3 moins la forme de v1 à midi (rapporté, jamais jugé) | les fenêtres d'événements | 920 | −0,46 pt | [−0,71 pt ; −0,21 pt] | meilleur |

| Horizon : la forme de v2 à midi moins la forme de v2 à minuit, rapporté, jamais jugé (_vu par les développeurs_) | Heures | Différence d'EAMP | IC 95 % |
|---|---|---|---|
| les heures propres à l'horizon (la demande, la forme de v2 à midi et celle à minuit) | 8 776 | +0,42 pt | [+0,33 pt ; +0,53 pt] |
| la queue froide | 181 | +0,30 pt | [−0,92 pt ; +0,74 pt] |
| les fenêtres d'événements | 920 | +0,64 pt | [+0,42 pt ; +0,87 pt] |

- Verdicts (_vu par les développeurs_) : termes « meilleur », famille « meilleur ».
- Jours en échec de qualité des données : 0 sur 366 ; recommandations du moniteur : réentraîner 1 fois.

Les heures les plus chargées de 2024 (rapportées, jamais jugées) :

| Heure de fin, UTC (_vu par les développeurs_) | Demande | Température prévue | v3 (production) | la forme de v2 à midi | la forme de v1 à midi | la forme de v2 à minuit |
|---|---|---|---|---|---|---|
| 2024-01-19 13:00 UTC | 36 348 MW | −17,8 °C | −0,8 % | −1,0 % | +1,0 % | −0,5 % |
| 2024-01-19 14:00 UTC | 35 967 MW | −17,2 °C | +0,6 % | +0,2 % | +0,7 % | +0,3 % |
| 2024-01-19 23:00 UTC | 35 717 MW | −14,6 °C | +1,0 % | +1,1 % | +2,5 % | +2,1 % |
| 2024-12-23 14:00 UTC | 35 711 MW | −12,0 °C | +7,5 % | +6,1 % | +6,8 % | +3,1 % |
| 2024-01-20 00:00 UTC | 35 668 MW | −15,0 °C | +0,8 % | +1,3 % | +2,3 % | +2,3 % |


| 2024, hiver (_vu par les développeurs_) | Jours | W | w | k | v3 (production) | la forme de v2 à midi | la forme de v1 à midi | Fenêtres non admissibles |
|---|---|---|---|---|---|---|---|---|
| 2023-24 | 2024-01-01 au 2024-03-31 | 230 | 176 | 23 | 15 sur 23 | 15 sur 23 | 15 sur 23 | 0 |
| 2024-25 | 2024-12-01 au 2024-12-31 | 230 | 54 | 7 | 5 sur 7 | 5 sur 7 | 5 sur 7 | 0 |



## 4. 2025, le test de référence

En attente. Le test de référence sera le premier rejeu de 2025 commencé sous `v3-rules-frozen`, après le test de référence de v2 et derrière la barrière de 2025 de v3 (section 11.3 du plan) ; aucun nombre de 2025 n'est montré avant.

## 5. Le registre des rejeux de v3

| Exécution | Année | Commencée | Étiquette | EAMP | Couverture | Termes | Famille | Position |
|---|---|---|---|---|---|---|---|---|
| `fe5d0e5bf99f411fa3c814f6ff51628c` | 2024 | 2026-10-28T17:37:41.982Z | _vu par les développeurs_ | 2,95 % | 92,1 % | meilleur | meilleur | aucune |

## 6. Limites

- Les termes, les classes d'intervalles et les fenêtres d'événements ont été choisis après avoir lu les résultats de v2 sur les années de validation : sur ces années, tout résultat est dans l'échantillon de ces choix ; seul 2025 peut les tester.
- Tout résultat qui lit la prévision ou la moyenne de l'avant-veille repose sur l'hypothèse de disponibilité (section 1).
- Les conditions actuelles du tarif sont appliquées à chaque hiver comme convention de notation, pas comme les conditions en vigueur à l'époque.
- Le vent n'entre que dans un terme ; il n'est pas surveillé pour la dérive.

## 7. Provenance

Exécutions de référence, nommées par `docs/freeze-v3.json` (commit `cc93edeba8fae4037a73e5f583de344375946fe8`, empreinte `4456590e4ca8`), sous l'étiquette `v3-rules-frozen` épinglée d'abord au commit `6ffedf1741ed`.
| Rôle | Exécution | Commit | Arbre propre | Étiquette de gel | Règles |
|---|---|---|---|---|---|
| qualité des données | `1871de5a164e44fea60013b53fa6bf4b` | `fa4683323cb5` | oui | `v3-rules-frozen` | `526e7ee3484c` |
| validation | `df111835244c4c40a323db65a96c25a2` | `fa4683323cb5` | oui | `v3-rules-frozen` | `526e7ee3484c` |
| modèles figés pour 2024 | `7f2ee1c96fb64a9ba492a57d60211f0e` | `fa4683323cb5` | oui | `v3-rules-frozen` | `526e7ee3484c` |
| répétition de 2024 | `fe5d0e5bf99f411fa3c814f6ff51628c` | `fa4683323cb5` | oui | `v3-rules-frozen` | `526e7ee3484c` |
| modèles figés pour 2025 | `95beb8658ae84eee90a990890dfff3fb` | `fa4683323cb5` | oui | `v3-rules-frozen` | `526e7ee3484c` |

- Vérification des copies à l'identique : réussie.
- Vérification de reproduction : réussie.
- Exports lus par l'exécution de qualité des données : Hydro-Québec récupéré le 2026-10-14, jeu de données modifié le 2026-06-05 ; ECCC récupéré du 2026-10-14 au 2026-10-14 ; NBM du 2026-10-17 au 2026-10-17.
