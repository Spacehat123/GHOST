# AGENCY MULTI-AGENT SPECIFICATION & OPERATIONAL PIPELINE
**Document:** `agents.md`  
**Director:** Ghost (Chief of Staff & Agency Technical Operations Director)  
**Goal:** High-Conversion Hospitality & Luxury Business Client Acquisition ($1,000+ Deals)  
**Architecture:** 4 Specialized Sub-Agents + Central Director + Ground Truth CRM  

---

## 1. The 5-Step Pipeline Lifecycle

```mermaid
flowchart LR
    Step1["Step 1: Recon & Lead Discovery<br/>(Maps / Social / Web Search)"] --> Step2["Step 2: Verification & Filtering<br/>(No modern site, $1k+ ticket check)"]
    Step2 --> Step3["Step 3: Bespoke Demo Production<br/>(OpenCode Parallel Builder + Smoke Test)"]
    Step3 --> Step4["Step 4: Contact Verification & Outreach<br/>(Verified Email + Tactical Auth Modal)"]
    Step4 --> Step5["Step 5: Ledger Ingestion & Follow-Ups<br/>(Phone/Contact in File + Watchdog Patrol)"]
```

1. **Step 1: Research & Discovery:** Scan designated regions/cities for high-end hospitality, boutique stays, and fine dining.
2. **Step 2: Filtration & Viability Gate:** Filter down to businesses that have *no website* (or only broken/PDF links or basic social pages) and command high average ticket sizes ($50+ per cover or $200+/night).
3. **Step 3: High-End Demo Generation:** Scaffold single-page luxury demos matching one of the 4 Archetypes, enforcing strict component standards and automated Brave CDP smoke tests.
4. **Step 4: Decision-Maker Contact & Verified Pitch:** Find the direct manager/owner email, verify MX records/inbox syntax, draft a personalized email, and present to the user via Tactical Authorization Modal before sending.
5. **Step 5: Central Contact & Phone Ledger:** Record verified phone numbers, emails, addresses, and owner details into both `data/contacts.json` (and `data/contacts.csv`) and Obsidian Vault client notes.

---

## 2. Specialized Multi-Agent Roles (The 4-Agent Division)

Instead of a single agent doing everything, the pipeline is divided across 4 specialized sub-agents orchestrated by Ghost:

```mermaid
flowchart TD
    Ghost["Ghost (Chief of Staff / Orchestrator)<br/>• Oversees pipeline state & turn budget<br/>• Enforces Tactical Authorization gates"]
    
    Ghost --> Agent1["Agent 1: Scout (Research Agent)<br/>Model: Gemini Web / Brave Search<br/>• Scrapes Google Maps, Yelp, Michelin, Instagram<br/>• Finds raw lists of 20–50 candidate businesses"]
    
    Agent1 --> Agent2["Agent 2: Inspector (Filter & Qualification Agent)<br/>Model: Fast Lightweight Evaluator<br/>• Checks DNS, domains, HTTP status, and menu prices<br/>• Filters list down to top qualified leads with NO real website"]
    
    Agent2 --> Agent3["Agent 3: Artisan (Demo Builder Swarm)<br/>Model: OpenCode (mimo-v2.6-flash-free)<br/>• Builds bespoke HTML/Tailwind demo based on Archetype<br/>• Runs Brave CDP smoke tests (390px & 1920px viewports)"]
    
    Agent3 --> Agent4["Agent 4: Courier (Outreach & CRM Specialist)<br/>Model: Ghost Core Engine<br/>• Verifies owner email & logs phone numbers to contacts ledger<br/>• Drafts high-converting pitch -> Triggers Tactical Modal<br/>• Dispatches verified email and tracks follow-up dates"]
```

### Agent 1: Scout (Research & Discovery)
- **Role:** Deep web reconnaissance.
- **Tools:** `web_search`, `brave_cdp` (for Google Maps & Instagram scraping).
- **Output:** Raw lead manifest containing business name, city, location, social links, and Google Maps URL.

### Agent 2: Inspector (Filter & Lead Qualification)
- **Role:** Lead validation and disqualification gate.
- **Criteria for Pass:**
  1. *No existing custom website* (or domain is dead, unmaintained, or points to a Facebook page/PDF menu).
  2. *High ticket size* ($$$ or $$$$ rating, tasting menu >$60, or room rates >$150/night).
  3. *Active business* (reviews within the last 30–60 days).
- **Output:** Qualified batch list passed directly to Agent 3.

### Agent 3: Artisan (High-End Demo Builder Swarm)
- **Role:** Parallel demo production.
- **Engine:** OpenCode CLI (`opencode/mimo-v2.6-flash-free`), 2 builds concurrently.
- **Design Guidelines:** Follows the 4 Luxury Archetypes in `chief.md` (no stock AI buttons, mobile category pills, subtle 1px borders, high-end typography, lazy-loaded media).
- **Verification:** Headless Brave CDP smoke test to ensure 0 console errors and clean mobile responsiveness.
- **Output:** Local path: `/home/pranc/projects/ghost/demos/<slug>/index.html`.

### Agent 4: Courier (Outreach & Contact Ledger Specialist)
- **Role:** Decision-maker contact extraction, ledger maintenance, and outreach.
- **Contact Ledger:** Automatically appends business name, decision-maker name, phone number, email, and social handles into:
  - Central CSV: `/home/pranc/projects/ghost/data/contacts.csv`
  - Central JSON: `/home/pranc/projects/ghost/data/contacts.json`
  - Obsidian Vault: `/home/pranc/Obsidian Vault/Ghost/Clients/<slug>.md`
- **Email Verification & Sending:**
  - Validates email format and domain MX record.
  - Generates personalized pitch referencing specific dishes/ambiance.
  - Pops the desktop **Tactical Authorization Modal** (`[✓ ALLOW]` / `[✕ DENY]`).
  - Upon approval, dispatches via authenticated SMTP (`pranav31207@gmail.com`) and sets follow-up timer (48–72h) in Watchdog.

---

## 3. How Agents Are Made & How They Work (Short Summary)

1. **How We Make Them:**
   - We implement each agent as an isolated Python class/worker in `src/ghost/workers/` (e.g., `scout.py`, `inspector.py`, `artisan.py`, `courier.py`).
   - Each worker has a narrow, dedicated prompt, a strict input/output JSON schema, and only the tools it needs (Scout gets web search; Artisan gets OpenCode + Brave CDP; Courier gets IMAP/SMTP + Contacts ledger).

2. **How They Work Together:**
   - Ghost (the Director) acts as the queue manager. Ghost passes the output of Agent 1 $\rightarrow$ Agent 2 $\rightarrow$ Agent 3 $\rightarrow$ Agent 4.
   - None of the sub-agents talk to each other directly or burn context. They write structured JSON outputs to `data/pipeline/<batch_id>/`, preserving Ghost’s tokens and eliminating hallucinations.
   - When Agent 4 is ready to pitch, it hands the draft back to Ghost to trigger the desktop authorization modal for you.
