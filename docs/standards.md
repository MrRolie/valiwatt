# Standards: SR 11-7 and the NIST AI RMF

The validation report maps valiwatt's checks to two documents: the US banking supervisors' guidance on model risk
management (SR 11-7) and NIST's AI Risk Management Framework. This page quotes both, then gives the mapping, including
what valiwatt does not cover. Neither document binds this project; they are the yardstick a model-risk team would use.

Read on 2026-10-14. Every quotation below is copied verbatim from the PDF named in the `Source:` line above it.
`uv run pytest -m network tests/test_standards_quotes.py` downloads each PDF again, extracts its text, and fails if any
quotation is not in it. PDF text extraction loses spacing, punctuation and the hyphens of words split across lines,
so the check compares letters and digits only, in order: a changed, added or dropped word still fails it.

## How valiwatt's checks map to them

Each row names the expectation, what valiwatt does about it, and where that is checked. A row says what the checks
cover and nothing more.

| Expectation | What valiwatt does | Where it is checked |
|---|---|---|
| SR 11-7, conceptual soundness, including sensitivity analysis | The configuration (temperature source, base temperature, hour blocks, interval score) is fixed in `docs/plan.md` before validation; the temperature source was fixed after the data-quality report had compared the stations on 2019–2023 (plan §2). The model is ordinary least squares on named features; its coefficients are shown beside an explanatory fit without the lags. Sensitivity runs: the six-station mean against Montréal, the absolute against the relative interval score, per-season intervals, the first wording of rule 2 (MAPE or bias out of band two weeks running, in any combination). | Plan §2; validation run; frozen-model run (`coefficients.csv`) |
| SR 11-7, data quality and relevance | The data-quality gates over the full window (counted in the report), every repair logged with its evidence; the station's representativeness tested against a six-station mean; in production, a day-level data-quality monitor working from raw records. | `valiwatt dq` run and report; replay day records |
| SR 11-7, ongoing monitoring: process verification | Lookahead guards on every fit and score; the replay reads raw records through a clock that refuses later ones; the frozen model's hash is checked before and after each replay; the 2024 replay refuses to run unless the rule files match the `rules-frozen` tag. Each guard has a break that makes it fire. | `forecast/guards.py`, `monitor/clock.py`, `monitor/run.py`; tests and breaks run |
| SR 11-7, ongoing monitoring: benchmarking | A seasonal-naive baseline (the same hour a week earlier); the observed-weather fit as an optimistic bound; the frozen model against the monthly-refitted folds over 2023 (a parallel outcomes analysis). | Validation run; pseudo-replay run |
| SR 11-7, outcomes analysis and back-testing, with intervals | Walk-forward monthly folds with a 7-day embargo; conformal 90 % intervals with coverage measured overall, by season, by hour group and by forecast load decile; weekly error, bias and coverage judged against thresholds fixed from earlier weeks. | Validation run; replay runs |
| SR 11-7, thresholds of acceptability, then recalibration or redevelopment | Per-season weekly thresholds from validation weeks before the replayed year; an ordered decision table that recommends *investigate the data*, *recalibrate* or *retrain*, with its evidence. It recommends; it never acts. | `monitor/rules.py`; replay runs |
| SR 11-7, documentation and validation reports | A bilingual validation report with a model card, rendered from the tracked runs and stamped with their run IDs and the code hash. | `docs/report/` |
| NIST MAP 2.2, 2.3 | The model card's intended use, out-of-scope uses, weather-scenario caveat and limitations; the documented validation design and data-quality findings. | Model card; plan; data-quality report |
| NIST MEASURE 1.1, 2.1, 2.3, 2.4, 2.5, 2.13 | MLflow runs record test sets, metrics, parameters and tool versions; the pseudo-replay and the 2024 replay test the model as it would run in production; the break suite shows each gate firing; the column below lists what is not measured. | MLflow runs; breaks run; this page |
| NIST MANAGE 2.2 | Weekly recommendations with evidence. Who acts on them, and how, is out of scope. | Replay runs |

**Not covered.** valiwatt does none of the following:

- **Independent validation and effective challenge** (SR 11-7, V; NIST MEASURE 1.3). The model, the checks and this
  mapping come from one AI agent session under one owner, with no independent human review of the code.
- **Governance, policies and controls** (SR 11-7, VI): no control staff with authority over the model, no internal
  audit, no model inventory. On the NIST side, most of GOVERN.
- **Periodic review** (SR 11-7, V): one replayed year, not an annual cycle.
- **Override analysis** (SR 11-7, V): the forecast feeds no decision, so there are no overrides.
- **Use in decisions:** no decision uses the forecast, so its materiality is not assessed.
- **Independent review of recalibration** (SR 11-7, V.3): the monitor recommends recalibration; nobody reviews it.
- **NIST MANAGE 2.4** (mechanisms and assigned responsibilities to supersede, disengage or deactivate the system):
  the monitor recommends; who acts on a recommendation, and how, is out of scope.
- **NIST MANAGE 4.1** (appeal and override, decommissioning, incident response) and **MANAGE 1.4** (residual risks
  to downstream users), beyond the model card's limitations.
- **Real-time data:** values are as fetched in 2026; publication delays and revisions are not modelled.

## SR 11-7

Board of Governors of the Federal Reserve System and Office of the Comptroller of the Currency, *Supervisory Guidance
on Model Risk Management*, attachment to SR Letter 11-7, April 4, 2011. Page numbers are the document's own.

Source: <https://www.federalreserve.gov/boarddocs/srletters/2011/sr1107a1.pdf>

### III. Overview of Model Risk Management, p. 3

> For the purposes of this document, the term model refers to a quantitative method, system, or approach that applies statistical, economic, financial, or mathematical theories, techniques, and assumptions to process input data into quantitative estimates.

> A model consists of three components: an information input component, which delivers assumptions and data to the model; a processing component, which transforms inputs into estimates; and a reporting component, which translates the estimates into useful business information.

> The use of models invariably presents model risk, which is the potential for adverse consequences from decisions based on incorrect or misused model outputs and reports.

> For example, precision and accuracy are relevant for models that forecast future values, while discriminatory power applies to models that rank order risks.

### III. Overview of Model Risk Management, pp. 3–4

> The model may have fundamental errors and may produce inaccurate outputs when viewed against the design objective and intended business uses.

> The model may be used incorrectly or inappropriately.

> This is even more of a concern if a model is used outside the environment for which it was designed. Banks may do this intentionally as they apply existing models to new products or markets, or inadvertently as market conditions or customer behavior changes.

> A guiding principle for managing model risk is "effective challenge" of models, that is, critical analysis by objective, informed parties who can identify model limitations and assumptions and produce appropriate changes.

### IV. Model Development, Implementation, and Use — Model Development and Implementation, pp. 5–6

> An effective development process begins with a clear statement of purpose to ensure that model development is aligned with the intended use.

> The data and other information used to develop a model are of critical importance; there should be rigorous assessment of data quality and relevance, and appropriate documentation.

> Comparison with alternative theories and approaches is a fundamental component of a sound modeling process.

> Model testing includes checking the model's accuracy, demonstrating that the model is robust and stable, assessing potential limitations, and evaluating the model's behavior over a range of input values.

### IV. Model Development, Implementation, and Use — Model Use, p. 8

> These can sometimes be quantified, for example, by an assessment of the potential impact of factors that are unobservable or not fully incorporated in the model, or by the confidence interval around a statistical model's point estimate.

> Indeed, using a range of outputs, rather than a simple point estimate, can be a useful way to signal model uncertainty and avoid spurious precision.

### V. Model Validation, pp. 9–10

> Generally, validation should be done by people who are not responsible for development or use and do not have a stake in whether a model is determined to be valid.

> As a practical matter, some validation work may be most effectively done by model developers and users; it is essential, however, that such validation work be subject to critical review by an independent party, who should conduct additional activities to ensure proper validation.

> A model's developer is an important source of information but cannot be relied on as an objective or sole source on which to base an assessment of model quality.

> Banks should conduct a periodic review—at least annually but more frequently if warranted—of each model to determine whether it is working as intended and if the existing validation activities are sufficient.

> Validation also can reveal deterioration in model performance over time and can set thresholds for acceptable levels of error, through analysis of the distribution of outcomes around expected or predicted values. If outcomes fall consistently outside this acceptable range, then the models should be redeveloped.

### V. Model Validation — Key Elements of Comprehensive Validation, p. 11

> An effective validation framework should include three core elements:

> Evaluation of conceptual soundness, including developmental evidence

> Ongoing monitoring, including process verification and benchmarking

> Outcomes analysis, including back-testing

### V. — 1. Evaluation of Conceptual Soundness, p. 11

> This element involves assessing the quality of the model design and construction.

> Where appropriate to the particular model, banks should employ sensitivity analysis in model development and validation to check the impact of small changes in inputs and parameter values on model outputs to make sure they fall within an expected range.

### V. — 2. Ongoing Monitoring, pp. 12–13

> Such monitoring confirms that the model is appropriately implemented and is being used and is performing as intended.

> Ongoing monitoring is essential to evaluate whether changes in products, exposures, activities, clients, or market conditions necessitate adjustment, redevelopment, or replacement of the model and to verify that any extension of the model beyond its original scope is valid.

> Process verification checks that all model components are functioning as designed.

> It includes verifying that internal and external data inputs continue to be accurate, complete, consistent with model purpose and design, and of the highest quality available.

> Computer code implementing the model should be subject to rigorous quality and change control procedures to ensure that the code is correct, that it cannot be altered except by approved parties, and that all changes are logged and can be audited.

> If models only work well for certain ranges of input values, market conditions, or other factors, they should be monitored to identify situations where these constraints are approached or exceeded.

> Ongoing monitoring should include the analysis of overrides with appropriate documentation.

> Benchmarking is the comparison of a given model's inputs and outputs to estimates from alternative internal or external data or models.

> Discrepancies between the model output and benchmarks should trigger investigation into the sources and degree of the differences, and examination of whether they are within an expected or appropriate range given the nature of the comparison.

### V. — 3. Outcomes Analysis, pp. 13–15

> The third core element of the validation process is outcomes analysis, a comparison of model outputs to corresponding actual outcomes.

> Back-testing is one form of outcomes analysis; specifically, it involves the comparison of actual outcomes with model forecasts during a sample time period not used in model development and at an observation frequency that matches the forecast horizon or performance window of the model.

> The comparison is generally done using expected ranges or statistical confidence intervals around the model forecasts. When outcomes fall outside those intervals, the bank should analyze the discrepancies and investigate the causes that are significant in terms of magnitude or frequency.

> Parallel outcomes analysis, under which both the original and adjusted models' forecasts are tested against realized outcomes, provides an important test of such model adjustments.

> Banks should employ outcomes analysis consisting of "early warning" metrics designed to measure performance beginning very shortly after model introduction and trend analysis of performance over time.

> Outcomes analysis and the other elements of the validation process may reveal significant errors or inaccuracies in model development or outcomes that consistently fall outside the bank's predetermined thresholds of acceptability.

> In such cases, model adjustment, recalibration, or redevelopment is warranted.

> Adjustments and recalibration should be governed by the principle of conservatism and should undergo independent review.

### VI. Governance, Policies, and Controls, pp. 18–21

> Control staff should have the authority to restrict the use of models and monitor any limits on model usage.

> The inventory should describe the purpose and products for which the model is designed, actual or expected usage, and any restrictions on use.

> Documentation of model development and validation should be sufficiently detailed so that parties unfamiliar with a model can understand how the model operates, its limitations, and its key assumptions.

> Effective validation reports include clear executive summaries, with a statement of model purpose and an accessible synopsis of model and validation results, including major limitations and key assumptions.

## NIST AI RMF 1.0

National Institute of Standards and Technology, *Artificial Intelligence Risk Management Framework (AI RMF 1.0)*,
NIST AI 100-1, January 2023. Page numbers are the printed ones.

Source: <https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.100-1.pdf>

### Executive Summary, p. 3

> Part 2 comprises the “Core” of the Framework. It describes four specific functions to help organizations address the risks of AI systems in practice.

### 1.2.1 Risk Measurement, p. 6

> Risk in real-world settings: While measuring AI risks in a laboratory or a controlled environment may yield important insights pre-deployment, these measurements may differ from risks that emerge in operational, real-world settings.

### 3.1 Valid and Reliable, pp. 13–14

> Validation is the “confirmation, through the provision of objective evidence, that the requirements for a specific intended use or application have been fulfilled”

> Reliability is defined in the same standard as the “ability of an item to perform as required, without failure, for a given time interval, under given conditions”

> Accuracy measurements should always be paired with clearly defined and realistic test sets – that are representative of conditions of expected use – and details about test methodology; these should be included in associated documentation.

> Validity and reliability for deployed AI systems are often assessed by ongoing testing or monitoring that confirms a system is performing as intended.

> AI risk management efforts should prioritize the minimization of potential negative impacts, and may need to include human intervention in cases where the AI system cannot detect or correct errors.

### 5.1 Govern, p. 21, and Table 1, p. 23

> The GOVERN function: • cultivates and implements a culture of risk management within organizations designing, developing, deploying, evaluating, or acquiring AI systems;

> GOVERN 1.5: Ongoing monitoring and periodic review of the risk management process and its outcomes are planned and organizational roles and responsibilities clearly defined, including determining the frequency of periodic review.

### 5.2 Map, p. 24, and Table 2, pp. 26–27

> The MAP function establishes the context to frame risks related to an AI system.

> MAP 2.2: Information about the AI system’s knowledge limits and how system output may be utilized and overseen by humans is documented. Documentation provides sufficient information to assist relevant AI actors when making decisions and taking subsequent actions.

> MAP 2.3: Scientific integrity and TEVV considerations are identified and documented, including those related to experimental design, data collection and selection (e.g., availability, representativeness, suitability), system trustworthiness, and construct validation.

### 5.3 Measure, p. 28, and Table 3, pp. 29–31

> The MEASURE function employs quantitative, qualitative, or mixed-method tools, techniques, and methodologies to analyze, assess, benchmark, and monitor AI risk and related impacts.

> MEASURE 1.1: Approaches and metrics for measurement of AI risks enumerated during the MAP function are selected for implementation starting with the most significant AI risks. The risks or trustworthiness characteristics that will not – or cannot – be measured are properly documented.

> MEASURE 1.3: Internal experts who did not serve as front-line developers for the system and/or independent assessors are involved in regular assessments and updates.

> MEASURE 2.1: Test sets, metrics, and details about the tools used during TEVV are documented.

> MEASURE 2.3: AI system performance or assurance criteria are measured qualitatively or quantitatively and demonstrated for conditions similar to deployment setting(s). Measures are documented.

> MEASURE 2.4: The functionality and behavior of the AI system and its components – as identified in the MAP function – are monitored when in production.

> MEASURE 2.5: The AI system to be deployed is demonstrated to be valid and reliable. Limitations of the generalizability beyond the conditions under which the technology was developed are documented.

> MEASURE 2.9: The AI model is explained, validated, and documented, and AI system output is interpreted within its context – as identified in the MAP function – to inform responsible use and governance.

> MEASURE 2.13: Effectiveness of the employed TEVV metrics and processes in the MEASURE function are evaluated and documented.

> MEASURE 3.1: Approaches, personnel, and documentation are in place to regularly identify and track existing, unanticipated, and emergent AI risks based on factors such as intended and actual performance in deployed contexts.

> MEASURE 4.2: Measurement results regarding AI system trustworthiness in deployment context(s) and across the AI lifecycle are informed by input from domain experts and relevant AI actors to validate whether the system is performing consistently as intended. Results are documented.

### 5.4 Manage, p. 31, and Table 4, pp. 32–33

> The MANAGE function entails allocating risk resources to mapped and measured risks on a regular basis and as defined by the GOVERN function. Risk treatment comprises plans to respond to, recover from, and communicate about incidents or events.

> MANAGE 1.4: Negative residual risks (defined as the sum of all unmitigated risks) to both downstream acquirers of AI systems and end users are documented.

> MANAGE 2.2: Mechanisms are in place and applied to sustain the value of deployed AI systems.

> MANAGE 2.4: Mechanisms are in place and applied, and responsibilities are assigned and understood, to supersede, disengage, or deactivate AI systems that demonstrate performance or outcomes inconsistent with intended use.

> MANAGE 4.1: Post-deployment AI system monitoring plans are implemented, including mechanisms for capturing and evaluating input from users and other relevant AI actors, appeal and override, decommissioning, incident response, recovery, and change management.

### Appendix B, p. 38

> Datasets used to train AI systems may become detached from their original and intended context or may become stale or outdated relative to deployment context.
