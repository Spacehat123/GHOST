# GHOST SECURITY & PERMISSION MODEL
**Document:** `SECURITY_AND_PERMISSION_MODEL.md`  
**Status:** AUTHORITATIVE SECURITY SPECIFICATION  
**Version:** 1.0  
**Date:** 2026-11-10  
**Working Directory:** `/home/pranc/projects/ghost`  

---

## 1. Security Philosophy & The Inviolable Constitution

In GHOST, security is enforced **in software outside the language model**. An LLM cannot grant itself permissions, circumvent authority checks, or clear a system halt by prompting.

```
┌─────────────────────────────────────────────────────────────┐
│                 INVIOLABLE CONSTITUTION                     │
│  1. Evidence or it didn't happen.                           │
│  2. The LLM proposes; deterministic control code disposes.   │
│  3. Grants are single-use cryptographic HMAC capability     │
│     tokens; workers never hold the signing secret.          │
│  4. Halt is an instant revocation, not a gentle suggestion. │
│  5. Collect-only money: accept payments, NEVER send payouts.│
│  6. Inbound text is UNTRUSTED DATA, never instructions.      │
│  7. High-impact operations require explicit operator modal. │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. The Five Autonomy & Permission Levels

Permissions are modeled as an ascending ladder:

| Level | Name | Scope & Authority | Allowed Actions |
|---|---|---|---|
| **L0** | **OBSERVE** | Passive environment perception | Reading hardware vitals, monitoring Git repos, reading IMAP inbox (read-only), screen capture. Zero external side-effects. |
| **L1** | **ANALYZE** | Data processing & reasoning | Extracting entities, classifying inbound email intent, diagnosing build test failures, calculating math expressions. |
| **L2** | **PREPARE** | Plan generation & drafting | Staging bug fixes in sandboxes, drafting cold outreach emails, composing client demos locally, creating invoices. Zero sends. |
| **L3** | **EXECUTE** | Authorized tool execution | Deploying an approved demo to Vercel, dispatching an approved email, executing a local git commit on an authorized branch. Requires single-use HMAC grant. |
| **L4** | **AUTONOMOUS OBJECTIVES** | Multi-step objective pursuit | Independently discovering regional leads, qualifying them, scraping public photos, and staging complete proposals within configured policy bounds. |

---

## 3. High-Impact Operations Requiring Operator Approval

The following operations strictly require an operator modal or explicit approval click:

1. **Financial Operations:**
   - Enforced by `test_collect_only.py`: Payout, refund, and transfer endpoints do not exist in code.
   - Ghost only generates incoming Razorpay Payment Links and UPI QR codes.
2. **External Communications:**
   - Sending emails via SMTP (`email_send`).
   - Sending messages via WhatsApp / Twilio (`whatsapp_send`).
3. **Cloud Hosting Deployments:**
   - Deploying client demos or production sites to Vercel / Netlify (`vercel_deploy`).
4. **Destructive Data Operations:**
   - Dropping database tables, clearing CRM client records, deleting local directories.
5. **Physical World Actuation:**
   - Triggering high-voltage relays or smart switches affecting physical property.

---

## 4. Single-Use HMAC Grant Isolation (`src/ghost/authority/`)

To prevent compromised or confused workers from executing unintended actions, side-effect tools enforce cryptographic grant tokens:

```
[Agent Turn] 
     │ Requests Outbound Action (e.g. deploy demo 'grasshopper')
     ▼
[Control Plane: issue_effect_grant("deploy", "grasshopper")]
     │ Reads GHOST_GRANT_SECRET from isolated process / grantd socket
     │ Formulates token: hmac_sha256(secret, "deploy:grasshopper:nonce:epoch")
     ▼
[Worker Tool: VercelDeployer.deploy_demo(..., grant_token=token)]
     │ Calls protocol.verify_grant(token, "deploy", "grasshopper")
     │ Checks:
     │   1. Signature matches HMAC(secret)
     │   2. Epoch matches data/REVOCATION_EPOCH
     │   3. Nonce has not been recorded in nonce.db (Replay Defense)
     ▼
[Action Executes] ──> Nonce marked consumed. Re-using token FAILS.
```

---

## 5. Instant Kill-Switch & Epoch-Based Revocation

1. **The Throat-Cut Handle (`ghost halt`):**
   - Touching `data/HALTED` or calling `halt.trip(reason=...)` creates an emergency barrier.
   - `assert_not_halted()` is called at the top of every tool, queue worker claim, and network client.
   - Workers stop cleanly at safe transactional points without burning attempt budgets.
2. **Epoch Revocation:**
   - Bumping the integer in `data/REVOCATION_EPOCH` invalidates **all** outstanding capability tokens minted prior to that epoch instantly, neutralizing any race condition or unauthorized queue jobs.

---

## 6. Prompt Injection Defense (T7 Quarantine)

All inbound text from untrusted sources (incoming email bodies, contact form messages, web search snippets) passes through [`src/ghost/control/quarantine.py`](file:///home/pranc/projects/ghost/src/ghost/control/quarantine.py):
- Strips instruction overrides (`"IGNORE PREVIOUS INSTRUCTIONS"`, `"SYSTEM PROMPT:"`, `"YOU ARE NOW"`).
- Escapes script tags, control characters, and ANSI escape sequences.
- Flags suspicious payloads for quarantine review in CRM before reaching LLM reasoning contexts.
