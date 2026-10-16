# Peut-on se fier à une prévision de la demande d'électricité du Québec ?

_La version courte du rapport de validation, en sept points : à lire d'abord._

> Données de demande : Hydro-Québec, « Historique de la demande d'électricité au Québec » (https://donnees.hydroquebec.com/explore/dataset/historique-demande-electricite-quebec/), sous licence CC BY-NC 4.0 (https://creativecommons.org/licenses/by-nc/4.0/). Hydro-Québec offre ces données à titre informatif et sans garantie de qualité ; ce ne sont pas des données officielles. Ce projet les a contrôlées, en a masqué ou corrigé quelques heures (chaque cas est documenté) et les a agrégées ; il ne redistribue pas le jeu de données, et les rapports n'en citent que quelques valeurs horaires, comme preuves.
>
> Données météorologiques : Environnement et Changement climatique Canada, utilisées sous la Licence du gouvernement ouvert – Canada (https://ouvert.canada.ca/fr/licence-du-gouvernement-ouvert-canada).
>
> Données extraites du site Web « Données climatiques historiques » d’Environnement et Changement climatique Canada ( https://climat.meteo.gc.ca/index_f.html ) le 2026-10-14.
>
> Ni Hydro-Québec ni Environnement et Changement climatique Canada n'ont produit, revu ou approuvé ce travail.
>
> Construit avec Claude Code sous la direction du propriétaire du dépôt.

Le [rapport complet](validation.fr.md) est la référence, et chaque point renvoie à sa section. Chaque nombre ici vient des mêmes exécutions MLflow : code `3a67ac3d2e3f`, rejeu de 2024 `9f87c124e34344a8a65e4a7679c3c458`. La version anglaise est dérivée de celle-ci.

## La question

Peut-on se fier à une prévision simple de la demande horaire d'électricité du Québec, et à quoi verrait-on qu'il faut cesser de s'y fier ? Ce projet ne cherche pas la meilleure prévision : il vérifie celle-ci comme le ferait une équipe de gestion du risque de modèle. Il mesure son erreur sur des mois qu'elle n'a pas vus, vérifie ce que valent ses fourchettes, puis rejoue une année entière comme en production, sous un moniteur qui peut recommander d'examiner les données, de recalibrer les fourchettes ou de réentraîner le modèle. Le moniteur recommande ; il n'agit jamais. ([section 1](validation.fr.md#1-les-méthodes-en-mots-simples))

## La prévision

À la fin de chaque journée, elle prévoit les 24 heures du lendemain à partir de l'heure et du type de jour, de la demande de la veille et de la semaine précédente, et de la température de la veille à Montréal, la seule connue quand la prévision est émise. Chaque mois de 2020 à 2023 est prévu par un modèle ajusté sur les seules heures qui le précèdent. Sur 2020–2023, la prévision s'écarte en moyenne de 4,42 % de la demande horaire, contre 7,81 % pour une prévision naïve (la même heure, une semaine avant) : elle fait mieux, et l'écart est confirmé. Avec la température réellement observée de l'heure visée, comme si la météo était parfaitement prévue, l'écart moyen serait de 3,40 % : c'est une borne optimiste, dont la prévision ne dispose pas. ([section 2](validation.fr.md#2-validation-sur-20202023))

## Où elle casse : le froid

Son écart moyen va de 2,61 % en été à 6,11 % en hiver ; par décile de charge prévue (dix tranches, de la plus faible à la plus forte), il va de 2,59 % (décile 1) à 5,98 % (décile 10). Aux 5 heures les plus chargées de 2019–2023, toutes pendant la pointe de froid de 2023-02, la demande est restée dans la fourchette à 90 % à 0 des 5 heures jugées ; elle l'a dépassée par le haut à 5. Même avec la température observée, la prévision reste de 10,8 % à 12,7 % sous la demande à ces heures ; la cause n'est pas testée, et une seule pointe est étudiée. ([section 2](validation.fr.md#2-validation-sur-20202023), [section 3](validation.fr.md#3-cas-tests))

![Pointe de froid](figures/validation/validation-peak-hours.fr.png)

## Quelle confiance lui accorder ?

Chaque prévision vient avec une fourchette qui promet de contenir la demande 90 % du temps. De 2021-01 à 2023-12, elle l'a contenue 90,6 % du temps : l'écart à la promesse reste dans l'incertitude de la mesure. Elle promet trop là où la demande en sort plus souvent que promis, au-delà de cette incertitude : l'hiver ; les déciles de charge prévue 7, 8, 9 et 10. Elle est plus large qu'il ne faut là où la demande reste dedans plus souvent que promis, au-delà de cette incertitude : l'été et l'automne ; les blocs horaires jour (10–15 h) et soir (16–20 h) ; les déciles de charge prévue 1, 2, 3, 4 et 5. Les seuils de couverture de l'hiver sont tirés de semaines qui avaient déjà ce défaut : le moniteur ne le signale pas comme une dégradation. ([section 4](validation.fr.md#4-intervalles-de-prévision), [section 8](validation.fr.md#8-limites))

## Le vrai test : 2024

L'année 2024 a ensuite été rejouée heure par heure, comme en production. Le modèle, figé sur les données jusqu'au 2023-12-24, n'a pas été réajusté, et chaque lecture passait par une horloge qui refuse toute donnée postérieure. Les règles du moniteur avaient été gelées avant le premier rejeu de 2024, puis changées une fois après que ses résultats eurent été vus (plus bas) ; ce rejeu a été refait après ce changement : il est **a posteriori**. L'écart moyen a été de 4,23 %, et la fourchette à 90 % a contenu la demande 89,4 % du temps. Le moniteur n'a rien recommandé de toute l'année. ([section 7](validation.fr.md#7-2024-rejouée-comme-en-production))

![Rejeu de 2024](figures/validation/replay-2024-weekly.fr.png)

## Le silence est-il une bonne nouvelle ?

Un moniteur muet ne rassure que s'il sait voir un vrai changement. Le rapport l'a mesuré en imposant des changements soutenus à quelques départs fixés et en rejouant les vraies règles. Sur 2024, un écart soutenu de ±6 % ou plus entre la demande et la prévision a donné « réentraîner » en 8 semaines au plus à chacun des 4 départs ; un écart de ±2 % l'a donné à 1 des 8 cas. La prévision s'appuie sur la demande de la veille : elle suit une hausse durable de la demande, et ne laisse entre les deux qu'un écart bien plus petit que la hausse elle-même. Une hausse soutenue de 10 % a donné « réentraîner » en 10 semaines à 0 des 4 départs sur 2023 (l'année de conception des règles), et à 1 des 4 sur 2024 (hors échantillon, mesure ajoutée après les résultats). Le silence n'est donc pas une preuve : des changements soutenus sont passés inaperçus à certains des départs mesurés. ([section 6](validation.fr.md#6-le-modèle-figé-répété-sur-2023), [section 7](validation.fr.md#7-2024-rejouée-comme-en-production))

## Le registre d'honnêteté

Les règles du moniteur ont été changées une fois, le 2026-10-14, après que les résultats de 2024 eurent été vus. La dérogation est consignée dans [`AGENTS.md`](../../AGENTS.md), et le rapport complet montre le rejeu d'avant à côté de celui d'après. Les rejeux d'avant et d'après ont appliqué le même modèle figé. Le résultat de 2024 n'a pas bougé : même écart moyen (4,23 %), même couverture (89,4 %) et aucune recommandation, avant comme après.

Chaque rejeu de 2024 terminé depuis la réécriture de l'historique consignée dans `AGENTS.md` est inscrit au registre des rejeux, [`docs/replay-ledger.json`](../replay-ledger.json) : une entrée n'est jamais réécrite, et un rejeu qui ne concorde pas avec son entrée fait refuser le rapport. ([section 9](validation.fr.md#9-les-bris-sur-des-données-synthétiques))

Ce que le rapport ne montre pas : une revue humaine indépendante du code (tout vient d'agents IA sous la direction d'un seul propriétaire), le taux de fausses alertes de dérive en 2024, les vagues de froid comme catégorie, ni les délais de publication ou les révisions des données. ([section 8](validation.fr.md#8-limites))
