# Data licences

Read on 2026-10-13. Every quotation below is copied verbatim from the page named in the `Source:` line above it
(spacing aside). `uv run pytest -m network tests/test_licence_quotes.py` fetches each page again and fails if any
quotation no longer appears on it.

This page records what the licences say. The repository's owner decides what they permit; his rulings are at the end.

## What this project does with the data

- Scripts download the data into `data/`, which git ignores. The data files are never committed and never
  redistributed.
- The repository publishes code, derived statistics, figures and reports.
- The README and every report carry the attribution lines kept in `src/valiwatt/attribution.py`.

## Hydro-Québec: hourly demand

The dataset "Historique de la demande d'électricité au Québec", as described by the open-data portal's API.

Source: <https://donnees.hydroquebec.com/api/explore/v2.1/catalog/datasets/historique-demande-electricite-quebec>

Licence field and licence URL:

> CC BY-NC 4.0

> https://creativecommons.org/licenses/by-nc/4.0/

From the dataset description (in French, as published):

> Elles sont offertes à titre informatif et ne peuvent être utilisées comme données officielles. Les données
> officielles (transmises à la Régie de l’énergie du Québec) sont disponibles dans le catalogue de données.

> La demande d’électricité pour une heure correspond à la demande totale moyenne pendant cette heure. Les données
> sont établies à la fin du pas de temps.

> Les données fournies sont brutes, sans garantie de qualité, et peuvent changer sans préavis.

The portal's footer also links Hydro-Québec's general terms of use for its digital platforms,
<https://www.hydroquebec.com/terms-confidentiality.html>. A script loads their text into the page, so they are not
quoted here.

### CC BY-NC 4.0

Source: <https://creativecommons.org/licenses/by-nc/4.0/legalcode.en>

Section 2(a)(1), the grant:

> Subject to the terms and conditions of this Public License, the Licensor hereby grants You a worldwide,
> royalty-free, non-sublicensable, non-exclusive, irrevocable license to exercise the Licensed Rights in the Licensed
> Material to: reproduce and Share the Licensed Material, in whole or in part, for NonCommercial purposes only; and
> produce, reproduce, and Share Adapted Material for NonCommercial purposes only.

Section 3(a)(1) and 3(a)(2), attribution:

> If You Share the Licensed Material (including in modified form), You must: retain the following if it is supplied
> by the Licensor with the Licensed Material: identification of the creator(s) of the Licensed Material and any
> others designated to receive attribution, in any reasonable manner requested by the Licensor (including by
> pseudonym if designated); a copyright notice; a notice that refers to this Public License; a notice that refers to
> the disclaimer of warranties; a URI or hyperlink to the Licensed Material to the extent reasonably practicable;
> indicate if You modified the Licensed Material and retain an indication of any previous modifications; and indicate
> the Licensed Material is licensed under this Public License, and include the text of, or the URI or hyperlink to,
> this Public License.

> You may satisfy the conditions in Section 3(a)(1) in any reasonable manner based on the medium, means, and context
> in which You Share the Licensed Material. For example, it may be reasonable to satisfy the conditions by providing
> a URI or hyperlink to a resource that includes the required information.

## Environment and Climate Change Canada: hourly weather

The observations come from the bulk CSV download of climate.weather.gc.ca. At least four texts bear on them, and they
do not agree.

### 1. The climate data site's "Data Licence Agreement"

The site's home page links it under this name:

Source: <https://climate.weather.gc.ca/index_e.html>

> Data Licence Agreement

Source: <https://climate.weather.gc.ca/prods_servs/attachment1_e.html>

> Date modified: 2026-08-10

> The Government of Canada (Environment and Climate Change Canada) is the owner of all intellectual property rights
> (including copyright) of this Software and Data Product. You are granted a limited, non-exclusive, non-assignable
> and non-transferable licence to use this software and data product subject to the terms below. This licence is not
> a sale of any or all of the owner's rights. This product may only be used by you, and you may not rent, lease,
> lend, sub-licence or transfer the data product or any of your rights under this agreement to anyone else, except
> under the following terms and conditions.

> You are authorized to further distribute the data or software, including any portion of it, contained in this
> product under the following conditions only. No fee will be charged explicitly for this Environment and Climate
> Change Canada product to any party to whom it is distributed. (Charges for value-added services are permitted.). In
> consideration of the licence you are herein granted, you have the obligation to acknowledge the source of the
> Environment and Climate Change Canada Data with the following layout or something similar: based on Environment
> and Climate Change Canada data. Redistribution must occur so that any other party must agree to the same
> redistribution restrictions before use of the redistributed product is allowed.

> Environment and Climate Change Canada does not warrant the quality, accuracy, or completeness of any information
> or data. Such information and data is provided "AS IS" without warranty or condition of any nature.

> It is YOUR RESPONSIBILITY to ensure that your use of this product complies with these terms and to seek prior
> written permission from Environment and Climate Change Canada and pay any additional fees or royalties, as may be
> required, for any uses not permitted or not specified in this agreement.

> Any use whatsoever of this Software and Data Product shall constitute your acceptance of the terms of this
> agreement.

The French version of the same agreement gives the attribution layout in French:

Source: <https://climate.weather.gc.ca/prods_servs/attachment1_f.html>

> créé à partir de données d’Environnement et Changement climatique Canada

The site's FAQ, also linked from its home page, gives the form a reference should take:

Source: <https://climate.weather.gc.ca/FAQ_e.html>

> How should I reference data? A. For data retrieved from the Historical Climate Data web site : “Extracted from the
> Environment and Climate Change Canada Historical Climate Data web site ( https://climate.weather.gc.ca/index_e.html )
> on [DATE]”.

Source: <https://climate.weather.gc.ca/FAQ_f.html>

> Pour les données extraites du site Web des données climatiques historiques : « Données extraites du site Web
> « Données climatiques historiques » d’Environnement et Changement climatique Canada (
> https://climat.meteo.gc.ca/index_f.html ) le [DATE] ».

Each report carries this reference with the date its data was retrieved.

### 2. The Open Government Licence – Canada

The Government of Canada's open-data catalogue lists ECCC records under this licence, including one whose resource
is the climate data site itself:

Source: <https://open.canada.ca/data/api/action/package_show?id=493966f9-f683-4e56-8fa6-8799999c00bd>

> Meteorological Observations

> Open Government Licence - Canada

> https://climate.weather.gc.ca

The catalogue's record for hourly observations carries the same licence. Its resources point at the MSC GeoMet API,
the MSC Datamart and the Climate Data Extraction Tool, not at the bulk download used here:

Source: <https://open.canada.ca/data/api/action/package_show?id=df2e6e1a-6057-4c4d-a509-94aa57705a8c>

> Hourly Climate Observations

> https://api.weather.gc.ca/collections/climate-hourly/items/

The licence itself:

Source: <https://open.canada.ca/en/open-government-licence-canada>

> The Information Provider grants you a worldwide, royalty-free, perpetual, non-exclusive licence to use the
> Information, including for commercial purposes, subject to the terms below.

> You are free to: Copy, modify, publish, translate, adapt, distribute or otherwise use the Information in any
> medium, mode or format for any lawful purpose.

> Acknowledge the source of the Information by including any attribution statement specified by the Information
> Provider(s) and, where possible, provide a link to this licence. If the Information Provider does not provide a
> specific attribution statement, or if you are using Information from several information providers and multiple
> attributions are not practical for your product or application, you must use the following attribution statement:
> Contains information licensed under the Open Government Licence – Canada.

> This licence does not grant you any right to use the Information in a way that suggests any official status or that
> the Information Provider endorses you or your use of the Information.

> The Information is licensed “as is”, and the Information Provider excludes all representations, warranties,
> obligations, and liabilities, whether express or implied, to the maximum extent permitted by law.

> This is version 2.0 of the Open Government Licence – Canada.

### 3. The MSC Data Services End-use Licence

The Meteorological Service of Canada publishes its own end-use licence for ECCC's data servers. The same hourly
observations are served by its GeoMet API (the `climate-hourly` collection above); `tests/test_live_sources.py`
checks that both routes return the same temperatures for a sample day.

Source: <https://eccc-msc.github.io/open-data/licence/readme_en/>

> Version 2.1.1 - August 2026

> The Information Provider grants you a worldwide, royalty-free, perpetual, non-exclusive licence to use the
> Information, including for commercial purposes, subject to the terms below.

> Copy, modify (see note), publish, translate, adapt (see note), distribute or otherwise use (see note) the
> Information in any medium, mode or format for any lawful purpose.

> Acknowledge the source of the Information by including any attribution statement specified by the Information
> Provider and/or Third Party Information Contributor and as indicated when you access the Information from
> Environment and Climate Change Canada (ECCC) data servers and, where possible, provide a link to this licence.

> Attribute data to the originator, including sources outside Environment and Climate Change Canada. For example;
> “Data Source: Environment and Climate Change Canada”

> This licence does not grant you any right to use the Information in a way that suggests any official status or that
> the Information Provider or Third Party Information Contributor endorses you or your use of the Information.

The page's version line says 2.1.1, while its versioning section says:

> This is version 2.1 of Environment and Climate Change Canada data servers End-use Licence.

### 4. The Government of Canada site's terms and conditions

Every page of climate.weather.gc.ca links these terms in its footer, as Canada.ca pages do.

Source: <https://www.canada.ca/en/transparency/terms.html>

> Unless otherwise specified you may reproduce the materials in whole or in part for non-commercial purposes, and in
> any format, without charge or further permission, provided you do the following: exercise due diligence in ensuring
> the accuracy of the materials reproduced indicate both the complete title of the materials reproduced, as well as
> the author (where available) indicate that the reproduction is a copy of the version available at [ URL where
> original document is available]

> Unless otherwise specified, you may not reproduce materials on this site, in whole or in part, for the purposes of
> commercial redistribution without prior written permission from the copyright administrator.

## The owner's rulings (2026-10-13)

- **ECCC: the Open Government Licence – Canada.** No page read here says which text governs `bulk_data_e.html`: the
  climate data site links a limited-use agreement (section 1), the open-data catalogue lists the same site under the
  Open Government Licence (section 2), the MSC publishes a third licence for its data servers (section 3), and every
  page carries the Government of Canada's site terms (section 4). The owner adopts the most permissive reading, the
  Open Government Licence, which the catalogue's "Meteorological Observations" record applies to the site. The README
  and every report attribute the data under that licence with a link to it, and each report also gives the reference
  in the form the site's FAQ asks for. The data itself still never enters git.
- **Hydro-Québec: NonCommercial.** The owner rules that this project's use is NonCommercial under CC BY-NC 4.0: it
  sells nothing and offers no paid service. The data is attributed as the licence asks (section on CC BY-NC 4.0) and
  is never redistributed; reports quote a few hourly values as evidence.
