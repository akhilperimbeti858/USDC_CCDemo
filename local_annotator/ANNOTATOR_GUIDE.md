# Annotator / Adjudicator Guide — Sanctions‑Screening NER Tool

*A start‑to‑finish walkthrough for the person doing the review. Written to be readable without a
technical background, but it does not skip the details you'll actually need — folder structure,
what each file means, and how your work gets saved.*

---

## 1. What this tool is (and what you'll be doing)

You'll open a **web page in your browser** that shows one document at a time. Your job is to:

1. **Highlight the important names** in the text (people, organizations, groups).
2. **Label each one** as **FTO**, **POI**, or **ORG**.
3. **Attach the matching OFAC (sanctions) ID** when the highlighted party is on the sanctions list.
4. **Mark the document reviewed** and move to the next one.
5. **Save** your work back into a shared folder so it can be used to train the model and (if needed)
   get a second, executive review.

There is **no software to install, no login, and no internet call** — the page runs entirely on your
machine and reads/writes files in a folder that syncs to SharePoint via OneDrive.

**The three labels:**

| Label | Key | Means | Color |
|---|---|---|---|
| **FTO** | `1` | Foreign Terrorist Organization | Blue |
| **POI** | `2` | Person of Interest (an individual) | Red |
| **ORG** | `3` | Organization / entity (company, etc.) | Green |

---

## 2. Before you start (one‑time setup)

1. **Use Google Chrome or Microsoft Edge.** The folder features do not work in Firefox/Safari.
2. **Sync the shared library with OneDrive.** In SharePoint, open the document library and click
   **Sync**. It will appear on your computer as a normal folder called **`ground-truth-annotation`**.
   - Right‑click that folder → **Always keep on this device** (so files are actually present, not
     just cloud placeholders).
3. **Get the tool file** — your team will give you the HTML file (e.g. `annotator_ofac_local.html`).
   Save it somewhere handy and **double‑click it** to open it in Chrome/Edge.

---

## 3. The folder structure (what lives where)

You will point the tool at **one** folder — the workspace named **`ground-truth-annotation`**. Inside
it, everything is organized like this:

```
ground-truth-annotation/           ← you open THIS folder in the tool
│
├─ AWS_RawOutput/                   ← INPUT: new batches to review (the model's first guess)
│     batch_1234.json
│     …
│
├─ reference/                       ← the OFAC sanctions list (a .csv with "ofac" in its name)
│     ofac_list.csv
│
└─ AnnotatedReview/                 ← OUTPUT: your work is written here
      │
      ├─ inprogress/                ← "Save to folder" drops your partial work here
      │     inprogress_<yourInitials>.json
      │
      ├─ completed/                 ← "Export reviewed" drops your finished work here
      │     ├─ reviewed/            reviewed_<yourInitials>.json
      │     └─ unreviewed/          unreviewed_<yourInitials>.json
      │
      └─ benchmark/                 ← Executive sign-off lands here (see §6)
            exec-reviewed_locked.json
```

**Rules to remember:**
- You **load** documents from **`AWS_RawOutput/`** (fresh work) or from **`AnnotatedReview/inprogress/`**
  (to resume something you saved earlier).
- You **never** load from `completed/` — that's finished, locked‑in work.
- The `AnnotatedReview/…` folders are **created automatically** the first time you save; you don't
  have to make them.
- A browser can only write **inside** the folder you open. That's why you open the whole
  **`ground-truth-annotation`** workspace — so your results (`AnnotatedReview/`) land right next to the
  input (`AWS_RawOutput/`).

---

## 4. Step‑by‑step: a normal review session

### Step 1 — Open the tool and connect the folder
- Double‑click the HTML file → it opens in your browser.
- Click **📂 Open folder** and choose the **`ground-truth-annotation`** folder, then **Allow** when
  the browser asks for permission.
- If you pick the wrong folder, the tool will say so and stop — pick `ground-truth-annotation`.
- When it's connected you'll see a green **🔒 ground-truth-annotation** tag confirming the workspace
  is locked in.
- *You'll be asked to choose the folder every time you open the tool — that's intentional.*

### Step 2 — Load the OFAC (sanctions) list
- The **Load OFAC list** button starts **red**. The tool usually **auto‑loads** the sanctions CSV from
  the workspace, and the button turns **green** with a count (e.g. "OFAC: 812 entities").
- If it stays red, click **Load OFAC list** and pick the CSV yourself.

### Step 3 — Pick a batch to review
- Use the dropdown (it lists the JSON files you're allowed to open) and choose a batch from
  **`AWS_RawOutput/`**.
- Click **⬇ Load**. The first document that still needs review appears.

### Step 4 — Enter your initials
- A prompt asks for your **initials** (up to 3 letters, e.g. `JPD`). These identify your work in the
  saved files. The **Job ID** is read automatically from the batch.

### Step 5 — Annotate the document
For each document:

- **Add a highlight:** select (drag over) the name in the text, then click a label button **or press
  its number key** (`1`=FTO, `2`=POI, `3`=ORG). A colored highlight appears and a card is added to the
  **Entities** panel on the right.
- **Remove a highlight:** hover the highlight and click the small **✕** that appears, or click **✕** on
  its card in the Entities panel.
- **Attach an OFAC ID** (this is the sanctions match):
  1. Click the **entity card** on the right to "target" it (it gets a highlighted outline).
  2. In the **OFAC list** panel, find the right entity and **click its row** — the OFAC ID is attached.
  3. Or click **edit** on the card to type an ID manually. Left blank, it stays `FILL` (unset).
- Overlapping highlights aren't allowed — remove and re‑add if you need to change one.

**Using the OFAC panel efficiently:**
- With the **search box empty**, it automatically shows the sanctioned entities **linked to this
  document's country**, grouped and ordered **FTO → POI → ORG**.
- To find something specific, **type a name or OFAC ID**. Use **Exact case** for precise matches.
- Use the **Category** dropdown (All / FTO / POI / ORG) to filter — and with the **search box empty**,
  picking a category (e.g. **POI**) **lists every entity of that type**, so you can browse the whole
  list, not only the ones linked to this document's country.
- Each result shows **Name (ID)** then **Type / Program / Countries / aka (aliases)** on separate lines.

### Step 6 — Mark the document reviewed
- When a document is done, click **Mark reviewed** (in the status bar under the title). The counter at
  the top shows **Reviewed X / N**.
- Move around with **◀ Prev / Next ▶**, jump with **Next unreviewed ⏭**, or group by country with the
  **All countries** dropdown and **Next unreviewed · country ⏭**.

### Step 7 — Save your progress (any time)
- Click **💾 Save to folder**. This writes your current state to
  **`AnnotatedReview/inprogress/inprogress_<yourInitials>.json`** (it overwrites your own file each
  time — no duplicates).
- Your browser **also autosaves** every edit locally: a small **Saved ✓** appears, so a refresh or
  crash won't lose your work.

### Step 8 — Resume later (same or different machine)
- Re‑open the tool → **📂 Open folder** → choose `ground-truth-annotation`.
- In the dropdown, pick your **`AnnotatedReview/inprogress/inprogress_<yourInitials>.json`** and
  **⬇ Load**. You'll pick up where you left off (it reopens at the first document still needing review).

### Step 9 — Finish and export
- When the batch is complete, click **⬇ Export reviewed**.
- You'll get a confirmation showing what will be written; click **OK**. The tool writes **two** files:
  - **`AnnotatedReview/completed/reviewed/reviewed_<yourInitials>.json`** — the documents you marked
    reviewed.
  - **`AnnotatedReview/completed/unreviewed/unreviewed_<yourInitials>.json`** — anything still pending.
- The page then **restarts** (so you're ready to pick the folder and the next batch cleanly).
- OneDrive syncs those files up to SharePoint automatically.

---

## 5. What's in a saved file (for the curious / technical)

Each saved JSON is a list of documents. A finished document looks like:

```json
{
  "file": "training_doc_CECGHHTE.txt",
  "text": "Acme Corp wired funds to Volkov Industries …",
  "humanReviewRequired": false,
  "country": "Iran",
  "annotatorID": "JPD",
  "entities": [ { "startOffset": 0, "endOffset": 9, "label": "ORG" } ],
  "metaData": [ { "startOffset": 0, "endOffset": 9, "confidence": 0.99, "ofacID": "OFAC_1001" } ]
}
```

- **`entities`** = your highlights (character positions + label). **`metaData`** runs in parallel and
  holds the **`ofacID`** you attached (omitted when left as `FILL`).
- **`humanReviewRequired`** = `false` once you mark it reviewed. This is how the tool knows to reopen at
  the first unfinished doc when you resume.
- **`annotatorID`** = your initials, stamped on docs you reviewed (so multiple reviewers' work can be
  merged and audited).
- **`country`** rides along so the tool can show the right OFAC subset.

You never edit this file by hand — the tool reads and writes it for you.

---

## 6. Executive / PO review (second‑level sign‑off)

There is a separate template, **`final_annotator_exec.html`**, for the **executive reviewer** (the
PO / adjudication lead). It works the same way, plus:

- A **🔒 Executive Reviewer** button asks for a **pass code**. Enter the exact code you were given; on
  success it greets you (**"Hello Amanda!"**) and switches into executive mode.
- **Executive mode** can open **any** file in the workspace (including `completed/`), and it shows
  **only documents that a first reviewer already finished** (`humanReviewRequired = false`).
- All the normal navigation still works — **Next unreviewed ⏭** and **Next unreviewed · country ⏭**
  jump to the next document still **awaiting executive review**; the status bar shows
  **"Awaiting exec: X · Locked: Y."**
- Review each one, then click **Mark Exec‑Reviewed (Lock)** — that stamps the document as
  **`ExecReviewed/Locked`** and removes it from the queue.
- Click **⬇ Export Exec‑Locked** to write the signed‑off documents to a dedicated **benchmark**
  folder: **`AnnotatedReview/benchmark/exec-reviewed_locked.json`** — the `_locked` marks them final.

If you are a normal annotator, you won't use this template — just the standard one.

---

## 7. Quick reference card

| Action | How |
|---|---|
| Add label | select text → click label **or** press `1`/`2`/`3` |
| Remove entity | hover highlight → **✕**, or **✕** on its card |
| Target an entity for OFAC | click its **card** |
| Attach OFAC ID | click an **OFAC row** (or **edit** to type one) |
| Search OFAC | type in the OFAC search box; use **Exact case** / **Category** |
| Mark done | **Mark reviewed** |
| Jump to next unfinished | **Next unreviewed ⏭** |
| Save partial work | **💾 Save to folder** → `inprogress/` |
| Finish a batch | **⬇ Export reviewed** → `completed/reviewed` + `unreviewed` |
| Resume | load your `inprogress/inprogress_<initials>.json` |

---

## 8. Troubleshooting & FAQ

- **"Please open the 'ground-truth-annotation' folder."** You picked the wrong folder — open the
  **whole workspace** folder, not `AWS_RawOutput` or a subfolder.
- **The Open folder button does nothing / says "needs Chrome or Edge."** Use **Chrome or Edge**, and
  run the file **locally** (double‑click) — this doesn't work embedded inside a SharePoint page.
- **Load OFAC list stays red.** The tool didn't find a sanctions CSV in the workspace — click **Load
  OFAC list** and select it manually (a `.csv` with `ofac` in the name).
- **My batch dropdown is empty / missing files.** As a normal reviewer you only see files in
  **`AWS_RawOutput/`** and **`AnnotatedReview/inprogress/`** — `completed/` is intentionally hidden.
- **Did I lose my work after a refresh?** No — the browser autosaves locally and offers to **restore**
  when you reload the same batch. For safety across machines, use **💾 Save to folder** regularly.
- **My changes aren't in SharePoint yet.** Give **OneDrive** a moment to sync the local folder up;
  confirm the file appears in `AnnotatedReview/…` on SharePoint.
- **I picked the wrong document / label.** Just fix it — remove the highlight and re‑add, or re‑open the
  OFAC editor. Nothing is final until you export (and, for executives, lock).

---

## 9. Glossary

- **NER** — Named Entity Recognition; the model that guesses the names, which you correct.
- **FTO / POI / ORG** — the three entity labels (see §1).
- **OFAC ID** — the identifier for a sanctioned party on the OFAC list; you attach it to a highlight
  when it's a true match.
- **`FILL`** — placeholder meaning "OFAC ID not set."
- **`humanReviewRequired`** — `true` = still needs review, `false` = you finished it.
- **`ExecReviewNeeded` / `ExecReviewed/Locked`** — executive‑level sign‑off fields (see §6).
- **Batch** — one JSON file of documents to review.
- **Workspace** — the `ground-truth-annotation` folder you open in the tool.

---

*Questions or something not matching what you see on screen? Flag it to your team lead — the tool and
this guide are versioned together, so it can be updated quickly.*
