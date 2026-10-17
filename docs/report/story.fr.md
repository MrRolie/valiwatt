# Peut-on se fier à une prévision de la demande d'électricité du Québec ?

_La version courte du rapport de validation, en sept points : à lire d'abord._

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

Le [rapport complet](validation.fr.md) est la référence, et chaque point renvoie à sa section. Chaque nombre ici vient des mêmes exécutions MLflow : code `c778e08fc0b5`, rejeu de 2024 `18daf1fc40a24300aeaba5e4f14d4107`. La version anglaise est dérivée de celle-ci.

## La question

**Statut : candidat.** Choisi sur 2020–2023 (dans l'échantillon du choix), montré sur 2024 vue par les développeurs ; le test sur données réservées, 2025, est en attente. Peut-on se fier à une prévision de la demande horaire d'électricité du Québec, et à quoi verrait-on qu'il faut cesser de s'y fier ? Ce projet ne cherche pas la meilleure prévision : il vérifie celle-ci comme le ferait une équipe de gestion du risque de modèle. Il mesure son erreur sur des mois qu'elle n'a pas vus, vérifie ce que valent ses fourchettes, puis rejoue des années entières comme en production, sous un moniteur qui peut recommander d'examiner les données, de recalibrer les fourchettes ou de réentraîner le modèle. Le moniteur recommande ; il n'agit jamais. ([section 1](validation.fr.md#1-les-méthodes-en-mots-simples))

## La prévision

À la fin de chaque journée, v2 prévoit les 24 heures du lendemain à partir d'une prévision de température : celle du modèle National Blend of Models de la NOAA pour l'aéroport de Montréal, déjà émise à ce moment, et non plus la température de la veille comme v1. Elle la traduit en demande par une courbe de température pour chaque heure de la journée, à côté de l'heure, du type de jour et de la demande de la veille et de la semaine précédente. Chaque mois de 2020 à 2023 est prévu par un modèle ajusté sur les seules heures qui le précèdent. Sur les 34 997 heures où les trois prévisions existent, l'écart moyen est de 2,83 % pour v2, de 3,48 % pour la forme de v1 sur la même prévision et de 4,42 % pour v1 tel que livré. Ces années ont servi à choisir v2 : l'avantage de v2 y est biaisé vers le haut par ce choix, et il ne prouve pas que v2 est meilleur. Avec la température réellement observée de l'heure visée, comme si la météo était parfaitement prévue, l'écart moyen de v2 serait de 2,70 % : une borne optimiste, dont la prévision ne dispose pas. ([section 2](validation.fr.md#2-lentrée-de-prévision), [section 3](validation.fr.md#3-validation-sur-20202023-dans-léchantillon-du-choix-de-la-famille-et-de-lentrée))

## Où elle casse : le froid

Son écart moyen va de 2,13 % en été à 3,56 % en hiver ; par décile de charge prévue (dix tranches, de la plus faible à la plus forte), il va de 1,98 % (décile 1) à 3,72 % (décile 9). Aux 5 heures les plus chargées de 2019–2023, toutes pendant la pointe de froid de 2023-02, la demande est restée dans la fourchette à 90 % à 2 des 5 heures jugées ; elle l'a dépassée par le haut à 3. Aux heures les plus froides, celles dont la prévision de température est sous −12,0 °C, l'écart de v2 est plus petit que celui de v1 tel que livré et plus petit que celui de la forme de v1, sur les années du choix ; une seule pointe est étudiée. ([section 3](validation.fr.md#3-validation-sur-20202023-dans-léchantillon-du-choix-de-la-famille-et-de-lentrée), [section 4](validation.fr.md#4-cas-tests))

![Pointe de froid](figures/validation/validation-peak-hours.fr.png)

## Quelle confiance lui accorder ?

Chaque prévision vient avec une fourchette qui promet de contenir la demande 90 % du temps. De 2021-01 à 2023-12, celle de v2 l'a contenue 90,3 % du temps : l'écart à la promesse reste dans l'incertitude de la mesure. Elle promet trop là où la demande en sort plus souvent que promis, au-delà de cette incertitude : l'hiver ; le bloc horaire jour (10–15 h) ; les déciles de charge prévue 7, 8, 9 et 10. Elle est plus large qu'il ne faut là où la demande reste dedans plus souvent que promis, au-delà de cette incertitude : l'été et l'automne ; les blocs horaires nuit (0–5 h) et fin de soirée (21–23 h) ; les déciles de charge prévue 1, 2, 3, 4 et 5. Les seuils de couverture de l'hiver sont tirés de semaines qui avaient déjà ce défaut : le moniteur ne le signale pas comme une dégradation. La couverture n'est jamais un critère du statut. ([section 5](validation.fr.md#5-intervalles-de-prévision), [section 10](validation.fr.md#10-limites))

## 2024, rejouée mais déjà vue

L'année 2024 a été rejouée heure par heure, comme en production. Le modèle, figé sur les données jusqu'au 2023-12-24, n'a pas été réajusté, et chaque lecture, prévisions de température comprises, passait par une horloge qui refuse toute donnée postérieure. L'écart moyen de v2 a été de 2,58 %, et sa fourchette à 90 % a contenu la demande 92,2 % du temps. Le moniteur n'a rien recommandé de toute l'année. Contre v1 tel que livré, le verdict est « meilleur » ; contre la forme de v1 sur la même prévision, « meilleur ». Mais 2024 n'est pas un test : les développeurs l'avaient vue à travers v1 avant de choisir v2. Un succès sur une année déjà vue est une preuve faible, qui ne soutient aucune affirmation. ([section 8](validation.fr.md#8-2024-rejouée-vu-par-les-développeurs))

![Rejeu de 2024](figures/validation/replay-2024-weekly.fr.png)

## Le silence est-il une bonne nouvelle ?

Un moniteur muet ne rassure que s'il sait voir un vrai changement. Le rapport l'a mesuré en imposant des changements soutenus à quelques départs fixés et en rejouant les vraies règles. Sur 2024, un écart soutenu de ±4 % ou plus entre la demande et la prévision a donné « réentraîner » en 8 semaines au plus à chacun des 4 départs ; un écart de ±2 % l'a donné à 4 des 8 cas. La prévision s'appuie sur la demande de la veille : elle suit une hausse durable de la demande, et ne laisse entre les deux qu'un écart bien plus petit que la hausse elle-même. Une hausse soutenue de 10 % a donné « réentraîner » en 10 semaines à 4 des 4 départs sur 2023 (l'année de répétition), et à 4 des 4 sur 2024 (vue par les développeurs). Le silence n'est donc pas une preuve : des changements soutenus sont passés inaperçus à certains des départs mesurés. ([section 7](validation.fr.md#7-le-modèle-figé-répété-sur-2023-répétition-sur-une-année-de-conception), [section 8](validation.fr.md#8-2024-rejouée-vu-par-les-développeurs))

## Le registre d'honnêteté

Ce qui a changé depuis v1 : l'entrée de température, une prévision au lieu de la veille, et le modèle, une courbe par heure. Les deux ont été choisis sur 2020–2023, après que les développeurs eurent vu les erreurs de v1 sur 2024, sa dérogation et un essai préliminaire de six familles de modèles sur ces mêmes années ; à ce moment, personne n'avait calculé un modèle de v2 sur 2024 ni lu une demande de 2025. C'est pourquoi 2020–2023 et 2024 ne prouvent rien : seul 2025 le peut. **En attente :** 2025 jugera, sur les données réservées, les verdicts contre v1 tel que livré et contre la forme de v1 sur la même prévision, les critères qui pourraient encore montrer que v2 a tort ; ils restent en attente tant que ces données n'existent pas. Les règles de v2 ont été gelées (étiquette `rules-frozen-v2`, commit `825b7bd134a8`) avant tout rejeu de v2, et aucune dérogation ne les a changées depuis. Chaque rejeu terminé est inscrit au registre des rejeux, [`docs/replay-ledger.json`](../replay-ledger.json), avec son étiquette d'évaluation : une entrée n'est jamais réécrite, et un rejeu qui ne concorde pas avec son entrée fait refuser le rapport. ([section 9](validation.fr.md#9-2025-le-test-de-référence))

Ce que le rapport ne montre pas : une revue humaine indépendante du code (tout vient d'agents IA sous la direction d'un seul propriétaire), la solidité du modèle de la NOAA lui-même, le taux de fausses alertes de dérive en 2024, ni les délais réels d'arrivée des prévisions, supposés. ([section 10](validation.fr.md#10-limites))
