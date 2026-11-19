# AI research layer

Status: **the contract and the output validator are written and tested; there is no Gemini adapter, no
orchestrator, and no way to run a research query.** The research routes answer `501`. The Gemini SDK
could not be installed, so its interface has not been inspected and no model id has been chosen. See
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

## Principle

The AI layer is an optional intelligence layer, never the source of truth. Everything else in VYRON works
with no key configured: the graph, search, timeline, repository parsing and every deterministic analysis.
With no `GEMINI_API_KEY` and `GEMINI_MODEL`, `Settings.ai_enabled` is false and AI panels are meant to show
an unavailable state rather than an error. (The panels are not written; the setting is.)

The model only ever sees evidence that VYRON retrieved, and it cannot assign provenance itself.

## What exists

| Part | Where | Tests |
| --- | --- | --- |
| Provider protocols: `AIProvider.generate(AIRequest) -> AIResponse`; `ResearchProvider.search(query, limit)` returning `SourceCandidate`s | `providers/ai.py`, `providers/research.py` | - |
| Typed failures: `AIProviderError`, `AIUnavailableError` | `providers/ai.py` | - |
| Strict parser and citation resolver for model output | `domain/research.py` | 10 |
| Claim, evidence and source rules (a `sourced` claim needs evidence; source URLs must be public http(s)) | `domain/evidence.py` | 12 |
| Tables for runs, the evidence a run was shown, claims and their citations | migration `0005` | linted, never run |
| Route contract for research and Future Lab (`POST /api/v1/research`, `GET /api/v1/research/{run_id}`, `POST /api/v1/future/scenarios`) | route registry, OpenAPI | contract only; they answer 501 |

Research kinds in the schema: `explain_entity`, `explain_relationship`, `compare`, `historical`,
`explain_repository`, `risk`, `research`, `graph_question`, `future_scenario`.

## How model output is handled (`parse_research_output`)

Model output is untrusted input. The parser takes the raw text and the set of evidence ids that were given
to the model for this run, and:

1. Refuses output over 200,000 characters, anything that is not a single JSON object (a Markdown code fence
   around it is allowed), duplicate keys, `NaN` and `Infinity`, and excessively deep nesting.
2. Requires `summary`; accepts `claims`, `connections`, `timeline` and `uncertainty` as lists of at most 100
   items. Text is Unicode-normalised, stripped of control characters, and length-limited (claims, notes and
   explanations 1,000 characters; summary 4,000). A wrong type is an error; nothing is coerced.
3. Resolves every citation: ids the run was shown are kept; **ids it was not shown are discarded and
   recorded** in `rejected_citation_ids`.
4. Assigns provenance itself. An item with at least one valid citation is `sourced`; an item with none is
   `ai_generated`. Anything the model says about its own provenance is ignored.
5. Takes no URL from the model at all: the model references evidence ids, and sources come only from
   providers. A year in a timeline note must be an integer from 1800 to 2200; a confidence must be a number
   from 0 to 1.

The database mirrors rule 3: `ai_research_claim_evidence` has a composite foreign key into
`ai_research_run_evidence`, so a stored citation to evidence the run never saw is refused by PostgreSQL
(UNVERIFIED until the migrations run). AI claims may not carry the provenance `seed`.

### What `sourced` does and does not mean

`sourced` here means **the claim cites evidence that was supplied to the model**. It does not mean the
evidence supports the claim: a model can cite a real source for a statement the source does not make.
The parser cannot detect that. The interface must therefore show the cited evidence beside each claim, and
should label claims without citations as not source-backed (`unsupported_claims` returns them). The
parser also does not check that a connection's `source` and `target` are real entity ids; whoever consumes
`ConnectionNote`s must.

## Prompting and prompt injection (design, not built)

Retrieved README text, web text and any other third-party content is passed to the model as clearly
delimited data and never as instructions. Output is requested as JSON against a schema and then parsed as
above; nothing the model returns is executed or interpreted beyond that. **No prompt exists yet, and
nothing has been tested against injection attempts**; that testing needs the real adapter and real
retrieved text.

## Future Lab

Scenario output is always labelled `predicted` (`is_prediction: true`). The timeline endpoints exclude
predictions unless `include_predictions=true`. The schema enforces `is_prediction = (provenance =
'predicted')`. The Future Lab itself is not built.

## Configuration

| Variable | Meaning |
| --- | --- |
| `GEMINI_API_KEY` | Server-side only. Empty disables AI features. |
| `GEMINI_MODEL` | A model id of the form `[A-Za-z0-9][A-Za-z0-9._-]{1,99}`. **No default is provided on purpose.** Choose one from Google's current models page and verify it before use. |

## To do, in order

1. Install the SDK and read the installed interface; the documentation reviewed on 2026-10-05 showed two
   surfaces (`generate_content` and a newer interactions API), so the adapter must target whichever the
   installed version supports.
2. Write the Gemini adapter behind `AIProvider`, with a timeout, a retry policy, an output-size cap, and
   redaction of the key from errors. Test it with recorded responses, plus one gated live test.
3. Write the orchestrator: retrieve evidence from the database, record it in `ai_research_run_evidence`,
   call the provider, run `parse_research_output`, store the run and claims, and enforce a per-user quota.
4. Write the prompts and an injection test set (hostile README text, instructions inside retrieved pages).
5. Build the research panel so that every claim shows its provenance label and its cited sources.
6. Decide what is logged: prompts and responses may contain user input and must not be kept by accident.
