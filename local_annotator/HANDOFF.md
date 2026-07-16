# Handoff — Off‑AWS NER Sanctions‑Screening Annotation Pipeline

*Presentation handoff & speaker notes. Built for a mixed audience: each section leads with a
**Top‑down** (plain‑English, for leadership/compliance) blurb, then a **Technical** blurb
(for engineers). Use the running order in §12 to build the deck.*

---

## 0. The headline (open and close with this)

> We reproduced SageMaker Ground Truth's human‑in‑the‑loop labeling for OFAC / sanctions‑screening
> NER **with no AWS dependency** and **at essentially zero incremental cost** — it runs on tools
> the org **already owns and already trusts**: a plain browser, **SharePoint / OneDrive**, and
> **Databricks** (ML only). No Cognito, no Ground Truth, no new vendor, no per‑annotation billing.

**Three points to repeat all the way through:**

1. **It's FREE.** The reviewer layer is a static HTML file + SharePoint/OneDrive the org already
   pays for. No AWS Ground Truth per‑object labeling fees, no Cognito user pool, no SageMaker
   endpoint, no data egress. Even the *optional* automation (Power Automate) is dramatically
   cheaper and more predictable than AWS billing.
2. **No AWS dependency.** The original blocker was Cognito (required by Ground Truth's private
   workforce) not being on the approved software list. We removed the *dependency*, not the
   *capability*.
3. **Accessible + unchanged downstream.** SharePoint is available to every reviewer with zero
   install, and the JSON in/out matches the existing Comprehend → partition → train contracts, so
   nothing in the Databricks pipeline had to change.

---

## 1. Why this exists (the problem slide)

- **Top‑down:** The plan was to use AWS SageMaker Ground Truth for human review. Ground Truth's
  private workforce requires **Amazon Cognito**, which is **not on the approved software list**.
  That single dependency stalled the whole human‑review step.
- **Technical:** Ground Truth's worker UI is a **Crowd‑HTML template** — highlight spans, an entity
  list, a per‑entity attribute field. That UI is *just HTML/JS*; the only thing forcing AWS was the
  **auth + task‑distribution + storage** layer (Cognito + GT + S3). We re‑implemented the UI as a
  standalone page and swapped the AWS backplane for **SharePoint/OneDrive**, keeping the exact data
  contracts so the ML side is untouched.

---

## 2. Cost & access — why FREE wins (put this early; it's the thesis)

| Capability | AWS / Ground Truth path | Our path | Incremental cost |
|---|---|---|---|
| Reviewer UI | Ground Truth private workforce (Cognito) | Offline HTML file | **$0** |
| Reviewer auth | Cognito user pool | SharePoint login they already have | **$0** |
| Task storage / handoff | S3 + GT manifests | SharePoint / OneDrive (already licensed) | **$0** |
| Save/load automation | — | Power Automate (free/seeded tier, or Premium) | **$0 → low, flat** |
| Per‑annotation labeling fee | GT charges **per object labeled** | none | **$0** |
| ML preprocessing/training | Databricks | Databricks (unchanged) | *(existing)* |

- **Top‑down:** The human‑review layer moved from a **metered AWS service** to **software we
  already own**. Power Automate — if we ever turn it on — is a small, flat cost, not a usage meter
  that scales with volume the way AWS Ground Truth + Cognito + S3 egress do.
- **Technical / the decoupling point:** reviewers **no longer need AWS accounts or broad Databricks
  permissions**. Previously, putting a human in the loop meant either AWS IAM/Cognito identities or
  Databricks workspace access (a permissions‑management burden and an audit surface). Now a reviewer
  needs **only SharePoint** — the review layer is fully **decoupled** from AWS billing policies and
  from Databricks entitlement management. Databricks stays where it belongs: ML compute, not human
  workflow.

---

## 3. Top‑down architecture (the map slide)

```
 ┌─────────────────────────── DATABRICKS (ML only) ───────────────────────────┐
 │  pull sentences → Comprehend recognizer (v7) → parse → BATCH JSON           │
 │            ▲                                              │                  │
 │            │ retrain (flywheel)                           │ drop to SharePoint
 │            │                                              ▼                  │
 │  TRAINING ◄── partition (≤5000B) ◄── CONSOLIDATION ◄── ANNOTATED JSON        │
 └──────────────────────────────▲───────────────────────────▲─────────────────┘
                                 │                           │
                       SharePoint / OneDrive        Offline HTML Annotator (browser)
                       (reviewed / unreviewed)      • label spans • OFAC IDs • review flags
                                 ▲                           │
                                 └───────── human review ────┘
        Active learning: low‑confidence / novel entities are routed back to review first
```

- **Top‑down:** Everything on the ML side is Databricks; everything a human touches is a browser +
  SharePoint. AWS never enters the loop.
- **Technical:** the boundary is a **file contract**, not an API coupling — Databricks writes
  `documents[]` with `initialEntities`/`metaData`; the annotator returns the same shape with
  `entities`/`metaData` and `humanReviewRequired`. Loose coupling means either side can change
  independently.

---

## 4. Offline UI Annotator *(core deliverable)*

- **Top‑down:** A single web page a reviewer opens in their browser. They highlight names,
  pick a category, attach the matching OFAC ID, and mark the document reviewed. Nothing to install,
  works with no internet, and their progress is never lost.
- **Technical:**
  - **Fully self‑contained** `annotator*.html` — HTML/CSS/JS inline, **no server, no CDN, no
    network, no build step**. Opens from `file://`.
  - **Character‑exact spans:** selections are mapped to offsets via `Range.toString().length`, so
    offsets stay correct even across nested highlight `<span>`s; overlapping spans are rejected.
  - **Fixed label schema** FTO / POI / ORG (keys 1–3), colors hardcoded; the batch's `labels`
    field is intentionally ignored for consistency.
  - **State model:** each doc carries `entities[]` + parallel `metaData[]` (`ofacID`, `confidence`),
    `humanReviewRequired`, optional `country`, `annotatorID`.
  - **Resume/safety:** `humanReviewRequired` round‑trips (re‑open resumes at the first pending doc);
    **localStorage autosave** keyed per Job ID guards against refresh/crash and offers restore.
  - **Attribution:** annotator **initials** + **Job ID** (from the payload) stamped on reviewed docs.

---

## 5. OFAC UI *(the sanctions‑screening differentiator)*

- **Top‑down:** Built into the annotator is a live **sanctions list**. When a reviewer opens a
  document, the tool automatically shows the sanctioned entities **linked to that document's
  country**, grouped by type, so they can attach the right OFAC ID in one click instead of
  searching a spreadsheet.
- **Technical:**
  - Loads the OFAC CSV (`ID, Type, Text, Program, Country`) — **one row per name/alias, grouped by
    ID**; countries single or `;`‑joined; a tolerant header/positional CSV parser.
  - **Country index** (`country → [ids]`): with an empty search box, the panel shows entities
    **linked to the current document's country**, **grouped and ordered FTO → POI → ORG** under
    colored headers.
  - **Category mapping heuristic:** `Program` contains `FTO` → **FTO**; `Type = Individual` →
    **POI**; else → **ORG**. Adjustable in one function.
  - **Search + filter:** name / alias / OFAC ID, case‑sensitive toggle, FTO/POI/ORG dropdown.
  - **Assignment:** click an entity card to target it, click an OFAC row → `ofacID` attached (or fill
    the manual modal). **No stale cache** — the list is loaded fresh each session; the *Load OFAC*
    button is **red until loaded, green after**.

---

## 6. Mirroring SageMaker Ground Truth *(parity, not a downgrade)*

- **Top‑down:** Reviewers get the same experience they'd have had in AWS — highlight, label, track
  progress — we only changed the plumbing behind it.
- **Technical parity table:**

| Ground Truth feature | Offline equivalent |
|---|---|
| Private‑workforce worker UI | Offline HTML annotator (no Cognito) |
| Crowd‑HTML span highlight + label | Same, char‑offset exact |
| Task queue / progress | Reviewed X/N, **Next unreviewed**, per‑country navigation |
| Per‑worker attribution | `annotatorID` (initials) stamped on reviewed docs |
| Output manifest (`.manifest`/JSON) | Annotated JSON — **identical field names** to the pipeline |
| Managed S3 storage | SharePoint / OneDrive |

  - **The key point:** feature‑for‑feature parity on what reviewers *do*; we replaced the
    *infrastructure*, not the *experience*.

---

## 7. SharePoint + OneDrive integration *(delivery — two tiers, pick by resources)*

- **Top‑down:** Two ways to get batches to reviewers and results back, depending on what licensing
  we want to use — one is fully free, one is a nicer embedded experience.
- **Tier 1 — Power Automate flows (embedded in the SharePoint page):**
  - **Save flow** (write, `Files/add(...,overwrite=true)` — updates in place) and **Get/List flow**
    (read — serves **both** batches *and* the OFAC list).
  - Browser calls are engineered around CORS: **save** = `text/plain` + `no-cors` (a CORS "simple
    request", no preflight — works inside the SharePoint iframe); **read** = plain GET where the
    flow's **Response sets `Access-Control-Allow-Origin`** so the page can read the reply.
  - **No Entra app registration** required. *Honest caveat:* the HTTP‑trigger connectors are
    **Premium** Power Automate.
- **Tier 2 — No‑Premium folder mode (File System Access API) — this is the FREE default:**
  - Reviewer **OneDrive‑syncs** the library and opens the **`ground-truth-annotation`** workspace
    folder locally in Chrome/Edge.
  - Lists every `*.json` recursively (loads a fresh batch from `AWS_RawOutput/` or resumes a saved
    file); writes results to a **sibling `AnnotatedReview/`** — `inprogress/inprogress_<initials>.json`,
    `completed/reviewed/reviewed_<initials>.json`, `completed/unreviewed/unreviewed_<initials>.json`.
  - **$0, no cloud service, no Premium** — OneDrive syncs the local writes up to SharePoint.
    Browser‑security note (worth stating): a page can only write **inside** the folder it's granted,
    which is why the reviewer opens the parent workspace so `AnnotatedReview/` lands beside
    `AWS_RawOutput/`.

---

## 8. SharePoint consolidation *(many reviewers → one training set)*

- **Top‑down:** Once reviewers finish, we automatically gather everyone's work from SharePoint,
  sort it into "found a match / no match" and "reviewed / not reviewed", and assemble the clean
  dataset the model trains on.
- **Technical:**
  - Ingests each reviewer's export, computes the **hit / no‑hit × reviewed / unreviewed** matrix,
    and performs a **multi‑reviewer merge** that preserves each `annotatorID`.
  - Emits the assembled **training set** ready for the ≤5000‑byte partition step. Lives in
    `consolidation/` — see `consolidation/README.md`. This is where distributed human review becomes
    a single, auditable dataset.

---

## 9. Databricks — preprocessing & training via flywheels

- **Top‑down:** Databricks does the machine‑learning heavy lifting: it prepares the documents for
  review, and after reviewers finish, it retrains the model on the newly‑labeled data — a loop that
  keeps getting smarter each round.
- **Technical:**
  - **Preprocessing:** pull sentences → **Amazon Comprehend custom‑entity recognizer (v7)** →
    parse `output.tar.gz` → **BATCH JSON** → drop to SharePoint.
  - **Post‑annotation:** consolidated annotations → **partition to ≤5000‑byte training docs**
    (`training_prep/partition.py`) → training job.
  - **Flywheel:** each round of human‑reviewed data **retrains the recognizer**, which generates the
    next batch — a continuous **data → model → better data** loop, orchestrated in
    `databricks/ner_pipeline_notebook.py`. Field names are identical across the pipeline, so the glue
    needs no translation.
  - **Cost framing:** Databricks remains for **compute**, but it no longer has to host the human
    workflow or hand reviewers workspace permissions — narrower blast radius, fewer entitlements.

---

## 10. Active learning *(spend review effort where it counts)*

- **Top‑down:** Instead of asking humans to check everything, we let the model flag the cases it's
  unsure about and send those to reviewers first. Over time humans review less while accuracy on the
  hard, high‑risk cases goes up.
- **Technical:**
  - Every candidate entity carries a model **confidence**; the loop prioritizes **low‑confidence /
    novel / newly‑sanctioned** spans for human review and can auto‑accept high‑confidence ones.
  - Each flywheel turn **reduces review burden** while **raising precision/recall** on exactly the
    edge cases sanctions screening cares about. Active learning is what makes the flywheel
    *cost‑effective*, not merely continuous.

---

## 11. Future / scaling — if we can leverage more resources *(the "where this goes" slide)*

The base solution is **free and complete today** (Tier‑2 folder mode). If the org chooses to invest
a little more, each tier adds polish — none is required, and all stay **far below AWS billing**:

| Option | What it unlocks | Cost / prerequisite | Best when |
|---|---|---|---|
| **Tier‑2 folder mode** *(today, default)* | Local browser + OneDrive sync; zero services | **$0** | Any time; no approvals needed |
| **Power Automate Premium** | Fully **embedded** save/load *inside* the SharePoint page; overwrite; batch/OFAC list served by flows | Premium PA license — **small, flat**, ≪ AWS metered | Want a seamless in‑SharePoint reviewer UX |
| **SPFx web part** | **Same‑origin** SharePoint REST using the reviewer's **own session** — no Premium, no Entra app; native SharePoint integration | Developer + **App Catalog** deploy (IT) | Want the richest, first‑class SharePoint integration |
| **Azure Logic Apps** | Same HTTP save/load pattern, off the Power Automate license | Azure consumption (pay‑per‑call, cheap) | Azure available but PA Premium isn't |
| **Microsoft Graph API + Entra app** | Direct programmatic SharePoint access; server‑side auto‑sync / dashboards | **Entra app registration** (IT‑gated) | Want automation beyond the browser |

- **Top‑down:** We can start free and scale up **only if** we decide the polish is worth it — and
  even the top tiers are cheaper and more predictable than AWS Ground Truth + Cognito + S3.
- **Technical / recommended path:** **SPFx** is the strongest long‑term target — same‑origin REST
  with the user's session means **no Premium and no app registration**, and it embeds natively in
  SharePoint. Power Automate Premium is the fastest interim upgrade (no code, no App Catalog). The
  decision is a **licensing/effort trade‑off**, not a capability gap — the pipeline already works
  without any of them.

---

## 12. Suggested slide running order (top‑down)

1. **Title** — Off‑AWS Sanctions‑Screening Annotation Pipeline
2. **The blocker** — Ground Truth needs Cognito → not approved (§1)
3. **The thesis: FREE + no AWS** — the cost/access table (§2)
4. **Architecture map** (§3)
5. **Offline annotator** — screenshot/demo (§4)
6. **OFAC UI** — screenshot/demo (§5)
7. **Parity with SageMaker** — the table (§6)
8. **SharePoint/OneDrive delivery** — two tiers (§7)
9. **Consolidation** (§8)
10. **Databricks flywheel** (§9)
11. **Active learning** (§10)
12. **Future / scaling** — PA Premium / SPFx / Graph (§11)
13. **Roadmap & asks** — licensing decision, reviewer rollout
14. **Close** — restate: *same capability, zero AWS, already‑approved tools, free.*

---

## 13. Technical appendix — data contracts (for the engineers in the room)

**Batch in** (Databricks → annotator):
```json
{
  "documents": [
    {
      "id": "training_doc_CECGHHTE.txt",
      "text": "Acme Corp wired funds to Volkov Industries …",
      "humanReviewRequired": true,
      "initialEntities": [{"startOffset": 0, "endOffset": 9, "label": "ORG"}],
      "metaData": [{"startOffset": 0, "endOffset": 9, "confidence": 0.99, "ofacID": "FILL"}]
    }
  ]
}
```
Optional payload fields: `job_id` (badge + autosave key), `country` / top‑level `countries` map,
embedded `ofacList`. `id` may be `file`; `text` may be `source`; offsets are **character offsets**.

**Annotated out** (annotator → consolidation):
```json
{
  "documents": [
    {
      "file": "training_doc_CECGHHTE.txt",
      "text": "…",
      "humanReviewRequired": false,
      "country": "Iran",
      "annotatorID": "JPD",
      "entities": [{"startOffset": 0, "endOffset": 9, "label": "ORG"}],
      "metaData": [{"startOffset": 0, "endOffset": 9, "confidence": 0.99, "ofacID": "OFAC_1001"}]
    }
  ]
}
```
`entities` and `metaData` are parallel; `ofacID` is emitted **only when set** (`FILL` dropped);
`confidence` is `null` for human‑added spans. This is the same contract the ≤5000‑byte partition
step already consumes — **no translation layer**.

**File variants shipped:**
- `annotator.html` / `annotator_badged.html` — base + name/Batch‑ID badge.
- `annotator_ofac.html` — OFAC panel + COUNTRY + download export.
- `annotator_ofac_sharepoint.html` — Power Automate save/load (Tier 1).
- `annotator_ofac_local.html` — **File System Access folder mode (Tier 2, free default)**.

---

## 14. Security & compliance notes (anticipate the questions)

- **No new external service** in the free tier — data never leaves the org's SharePoint/OneDrive
  tenant; the annotator makes **no network calls**.
- **Least privilege:** reviewers need only SharePoint — no AWS IAM/Cognito identities, no Databricks
  workspace grants.
- **Auditability:** every reviewed doc is stamped with `annotatorID`; consolidation preserves it.
- **If Power Automate is enabled:** flow trigger URLs are **bearer secrets** — keep private, and
  gate the read flow with a shared‑secret condition (it returns file contents).

---

## 15. Closing value restatement (last slide)

- **FREE.** Static HTML + SharePoint/OneDrive we already own; no per‑annotation AWS billing.
- **No AWS dependency.** The Cognito blocker is gone — nothing to procure or approve.
- **Accessible.** A browser + SharePoint — every reviewer is ready today.
- **Unchanged downstream.** Identical data contracts → the Databricks flywheel just works.
- **Self‑improving.** Human review → consolidation → retrain → active learning → less review, better
  model.
- **Room to grow.** Optional Power Automate / SPFx upgrades add polish — still far below AWS cost.

---
---

# Part B — Full narrative deck (slide‑by‑slide)

*Each slide: **Top‑down** (what a non‑technical stakeholder should take away) then **Technical**
(the detail for engineers). A couple of terms are inferred from context — see the **terminology
note** at the end; swap in your team's internal wording.*

## B1. Title
**"A SharePoint‑Native, Zero‑Cost Alternative to SageMaker Ground Truth — for OFAC / Sanctions NER."**
- Subtitle: *Give case adjudicators the labeling experience of Ground Truth, without AWS, without
  new licenses, without per‑annotation billing.*

## B2. What I built — before / after
- **Top‑down:** We replaced a stalled, AWS‑dependent labeling setup with a browser page + SharePoint
  that anyone on the team can use today.

| | **Before** | **After** |
|---|---|---|
| Reviewer UI | SageMaker Ground Truth (blocked) | Offline HTML annotator |
| Identity | Cognito user pool / AWS IAM seats | Existing SharePoint login |
| Data handoff | S3 + GT manifests | SharePoint / OneDrive |
| Cost model | per‑object labeling + Cognito + S3 egress | **$0 incremental** |
| Onboarding a reviewer | IAM/Cognito provisioning ticket | share a SharePoint folder |
| Downstream ML | Comprehend Custom NER | **unchanged** (same JSON contract) |

- **Technical:** the before/after is a **backplane swap** — the Crowd‑HTML labeling surface is
  preserved; only auth/queue/storage moved from AWS to SharePoint. Data contracts are byte‑compatible
  so Comprehend Custom NER training is untouched.

## B3. Why it matters — business value
- **Top‑down:** More adjudicators can label, sooner, at no added cost, and the labels still make the
  model better.
- **Technical / value levers:** (1) **Cost** — eliminates GT per‑object fees + Cognito + egress;
  (2) **Access** — removes IAM/Cognito onboarding friction (no seat provisioning); (3) **Throughput**
  — any adjudicator with SharePoint can contribute, so labeling scales with people, not licenses;
  (4) **Continuity** — labels feed the same Comprehend Custom NER flywheel.

## B4. Problem / motivation — why adjudicators needed access *without* AWS
- **Top‑down:** The people who actually understand sanctions cases (case adjudicators) couldn't easily
  get into the AWS tool — and every new reviewer meant cost and IT tickets.
- **Technical — "the gap" callout:**
  - **Cost:** Ground Truth bills **per labeled object**; Cognito + S3 add standing costs.
  - **Licensing seats:** each reviewer is an identity to license/manage.
  - **IAM provisioning friction:** onboarding a reviewer = IAM/Cognito request → approval → config;
    slow, and a recurring audit surface.
  - **The gap:** the expertise (adjudicators) sits *outside* the group with easy AWS access. We needed
    to bring the tool **to where the experts already are** — SharePoint.

## B5. Architecture overview (offline UI + SharePoint backend)
- **Top‑down:** Databricks prepares documents → they land in SharePoint → adjudicators label them in
  the browser tool → results sync back to SharePoint → Databricks retrains the model.
- **Technical:** `SharePoint (AWS_RawOutput/) → Offline UI (browser) → sync → AnnotatedReview/ →
  Consolidation → Comprehend Custom NER (retrain)`. What it **mirrors from Ground Truth**: entity
  classes (FTO/POI/ORG), **span annotation** (char‑exact offsets), a **review workflow**
  (needs‑review → reviewed, resumable), and per‑worker attribution (`annotatorID`).

## B6. The old manual process (conceptual, step‑by‑step)
- **Top‑down:** Today, matching names against sanctions lists is slow and manual.
- **Conceptual steps (illustrative):**
  1. Analyst receives a document / transaction record.
  2. Eyeballs it for names, orgs, aliases.
  3. Manually searches the OFAC list (spreadsheet / portal) for each candidate.
  4. Judges whether context indicates a true match vs. a coincidence of names.
  5. Records the decision in a case system — **no reusable labeled data produced**.
- **Technical takeaway:** the manual loop throws away the very signal (labeled spans + context) an ML
  model needs. Our tool **captures that judgment as training data** as a byproduct of the review.

## B7. New workflow & stakeholders (OFAC vs. TRIG Tier‑1; why expertise matters)
- **Top‑down:** The new loop routes documents to the right expert and turns their decisions into
  training data automatically.
- **Stakeholders & routing:**
  - **OFAC screening** — sanctions matches (SDN/consolidated lists); adjudicators confirm identity vs.
    coincidence.
  - **TRIG Tier‑1** *(internal watchlist tier — confirm exact definition with your team)* — a distinct,
    higher‑scrutiny tier requiring **more experienced adjudicators**; the distinction matters because
    a Tier‑1 determination carries more risk and needs deeper subject‑matter judgment.
  - **Why adjudicator expertise matters:** name overlap is common; only an expert reliably separates a
    true sanctioned party from an innocent namesake using **context** — which is exactly the signal we
    capture and teach the model.
- **Technical:** documents can be tagged by `country` / program so the tool surfaces the relevant
  OFAC subset; reviewer identity (`annotatorID`) preserves who adjudicated what for audit.

## B8. Annotation methodology — context‑matching
- **Top‑down:** Adjudicators don't just tag a name — they tag it **because the surrounding text
  confirms it's the sanctioned party**. That context is the lesson the model learns.
- **Technical:** annotation is **span + class + OFAC‑ID linkage in situ**. Because the label lives in
  the original sentence, each example teaches the model the **contextual pattern** (co‑text: roles,
  locations, transaction verbs) that distinguishes a true entity from a namesake — not just the string.

## B9. Country distribution + why imbalance matters *(native bar chart — illustrative numbers)*
- **Top‑down:** Our documents skew heavily toward a few countries. If we train naively, the model gets
  great at the common ones and weak at the rare‑but‑critical ones.

```
Labeled entities by country (illustrative)
Iran       ██████████████████████████████  1,240
Russia     █████████████████               720
Syria      ██████████                       410
N. Korea   ██████                           260
Cuba       ████                             150
Venezuela  ███                              110
Other      ██                                90
```

- **Why imbalance matters (technical):** a classifier trained on this distribution minimizes overall
  loss by favoring the majority classes → **high accuracy on Iran/Russia, poor recall on rare
  countries** — the opposite of what sanctions risk wants. Mitigations we apply: **stratified /
  balanced sampling**, **class weighting**, and **hard‑negative + positive‑pair** construction (next
  slide) so rare‑country and look‑alike cases are represented in training.

## B10. Positive vs. negative pair training *(mock excerpt, tied to the example entity)*
- **Top‑down:** We teach the model with matched examples — one where the name **is** the sanctioned
  party, one where a look‑alike **is not** — so it learns to judge by context, not spelling.
- **Mock excerpts (fictional, entity = "Nemesio Osguera"):**
  - **Positive (label = POI, OFAC match):**
    > "…wire of **$4.2M routed through a shell company controlled by Nemesio Osguera**, flagged under
    > program SDNTK…"
    *Context signals: control of a shell company, illicit transfer, program tag → true sanctioned
    individual.*
  - **Hard negative (no label):**
    > "…the conference keynote was delivered by **Dr. Nemesio Osguera**, a marine biologist at the
    > university…"
    *Same string, benign context → NOT the sanctioned party.*
- **Technical:** contrastive **positive/negative pairs** force the model to rely on **contextual
  embeddings** rather than surface form. Hard negatives (same name, different context) are the
  highest‑value examples for cutting false positives — the dominant cost in screening.

## B11. Transfer learning explainer (frozen base → fine‑tuned head)
- **Top‑down:** We don't teach the model language from scratch. We start from a model that already
  "reads," and only train a thin final layer to recognize our sanctions entities.
- **Technical:**
  - **Frozen base:** a pretrained language model provides general contextual representations
    (weights **frozen** — not updated).
  - **Fine‑tuned classification head:** only the **top classification layer** is trained on our
    labeled spans (FTO/POI/ORG + OFAC linkage).
  - **Why:** needs **far less labeled data**, trains fast/cheap, and inherits the base model's
    generalization — so a modest set of adjudicator labels goes a long way. (Conceptually mirrors how
    Comprehend Custom NER adapts a strong base recognizer to a custom entity set.)

```
[ Pretrained base LM (frozen) ] → contextual embeddings → [ small trained head ] → FTO / POI / ORG
```

## B12. The Nemesio Osguera walkthrough (snippet → model output → why it generalizes)
- **Top‑down:** Even for a name spelling the model has **never seen before**, it can still flag the
  sanctioned party — because it learned the *context*, not the exact letters.
- **Fictional document snippet (unseen alias "N. Osguera‑Vela"):**
  > "Funds were layered through three intermediaries before settling in an account beneficially owned
  > by **N. Osguera‑Vela**, consistent with prior structuring activity."
- **Model output (illustrative):**
  ```json
  { "text": "N. Osguera-Vela", "label": "POI", "confidence": 0.91, "ofacID": "OFAC_2050" }
  ```
- **Plain‑language why it generalizes:** the model never saw "N. Osguera‑Vela," but the **surrounding
  context** (beneficial ownership, layering, structuring) matches the *positive* patterns it learned,
  and the contextual embedding of the alias is close to the known entity's — so it flags it and links
  the OFAC ID. **Surface‑form memorization can't do this; context‑based transfer learning can.**

## B13. Why accuracy improves (tie the three threads together)
- **Top‑down:** Better data balance + teaching by context + starting from a strong base model = a
  screener that catches the hard cases and the aliases, with fewer false alarms.
- **Technical — the three threads:**
  1. **Balanced/represented data (B9)** → recall on rare, high‑risk countries.
  2. **Positive/negative pairs (B10)** → context over spelling → **fewer false positives** on namesakes.
  3. **Transfer learning (B11)** → strong priors → **generalization to unseen aliases (B12)**.
  Together they move the model from "matches strings" to "**adjudicates identity in context**."

## B14. Side‑by‑side — offline tool vs. SageMaker Ground Truth
| Dimension | Ground Truth | Offline UI | Tradeoff |
|---|---|---|---|
| Reviewer auth | Cognito | SharePoint SSO | ✅ no new identities |
| Span labeling | Crowd‑HTML | same, char‑exact | ✅ parity |
| Task queue | managed | Reviewed X/N + next‑unreviewed | ✅ parity (lighter) |
| Storage | S3 | SharePoint/OneDrive | ✅ already owned |
| Scaling to many workers | licensed | share a folder | ✅ people, not seats |
| Fully managed pipeline | yes | we operate it | ⚠️ we own the glue |
| Advanced GT features (consolidation UI, active‑learning built‑in) | yes | we implement | ⚠️ our consolidation + AL |
- **Honest tradeoff line:** we trade "fully managed" for "free, accessible, and no AWS dependency" —
  the right trade given the Cognito blocker and cost profile.

## B15. Demo walkthrough / **live demo transition**
- **Transition line:** *"Let's see an adjudicator label a document and watch it sync to SharePoint."*
- **Demo beats (schematic; swap real screenshots later):**
  1. Open the annotator → **Open folder** (`ground-truth-annotation`).
  2. Load a batch from `AWS_RawOutput/`; country‑linked OFAC list appears.
  3. Highlight a name → label POI → attach OFAC ID in one click.
  4. **Mark reviewed** → **Save to folder** → file appears in `AnnotatedReview/inprogress/`.
  5. **Export reviewed** → `completed/reviewed/` + `unreviewed/` → OneDrive syncs to SharePoint.

## B16. Impact
- **Top‑down:** More experts labeling, less money and infra, and every label still improves the model.
- **Technical / measurable:**
  - **Adjudicator access expansion** — from "AWS‑provisioned few" to "any SharePoint user."
  - **Cost / infra savings** — no GT per‑object fees, no Cognito, no S3 egress; review layer $0.
  - **Feedback loop intact** — labeled data flows to **Comprehend Custom NER** training unchanged.

## B17. Next steps / roadmap
- Pilot with a small adjudicator group (Tier‑1 + OFAC).
- Decide the delivery tier: stay free (folder mode) vs. **Power Automate Premium** vs. **SPFx**.
- Wire consolidation → scheduled Comprehend Custom NER retrain (close the flywheel).
- Add active‑learning prioritization (low‑confidence / novel aliases first).
- Metrics: precision/recall by country & program, reviewer throughput, false‑positive rate.

---

# Part C — Six‑slide conceptual deck (diagram‑first, no screenshots)

*The condensed version for a short readout. Intentionally schematic — drop in real mockups later
without rebuilding.*

1. **Title** — *"SharePoint‑Native Annotation: a Zero‑Cost Alternative to Ground Truth."*
2. **Why an offline alternative** — the **IAM/onboarding friction** problem; big **"the gap"**
   callout: *experts (adjudicators) sit outside easy AWS access; cost + seats + provisioning block
   them.*
3. **Architecture overview** — `SharePoint → Offline UI → Sync → Comprehend NER`, annotated with
   *"mirrors Ground Truth: entity classes, span annotation, review workflow."*
4. **Feature comparison** — Ground Truth vs. Offline UI (condensed B14 table).
5. **Adjudicator workflow** — the **four‑step annotation‑to‑training loop**:
   `Label in browser → Sync to SharePoint → Consolidate → Retrain Comprehend NER → (repeat)`.
6. **Impact & next steps** — stat callouts (**$0 review layer**, **adjudicator access ↑**, **flywheel
   intact**) + pilot/rollout list (Tier‑1 + OFAC pilot → tier decision → scheduled retrain → active
   learning).

---

## Terminology & scope notes (read before presenting)
- **"Case adjudicators"** and the **AWS‑dependency framing** are inferred from context — confirm they
  match your team's internal wording for reviewers.
- **"TRIG Tier‑1"** is treated as an internal, higher‑scrutiny watchlist tier distinct from OFAC —
  **verify the exact definition/name** with your team and adjust.
- The **country numbers (B9)**, **mock excerpts (B10/B12)**, and **model outputs** are **illustrative
  placeholders** — replace with real figures/snippets when available.
- Part C slides are **schematic by request** (no real screenshots); real mockups can be swapped in
  later without changing the structure.
