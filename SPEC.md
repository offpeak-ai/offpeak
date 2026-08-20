# The Deadline Spec — v0.1 (draft)

How software says **"this can wait."**

Status: draft. The spec is versioned with the library; breaking changes bump the minor version while in 0.x. Changes start as issues on this repository.

## 1. Why a spec

Every inference API today has exactly one urgency: now. Venues already price patience (public batch tiers at 50% of list), but no annotation exists for a caller to express it. This spec defines that annotation — the *deadline* — so that any client, gateway, queue, or scheduler can carry it, and any venue can honor it.

## 2. The deadline

A **deadline** is the instant by which a job's result must be available to the caller. It is a property of the *job*, not of the venue or transport.

### 2.1 Forms

Producers MAY express a deadline in any of these forms; consumers MUST resolve them to an absolute, timezone-aware instant at ingestion time:

| Form | Example | Resolution rule |
| --- | --- | --- |
| Wall-clock | `"06:00"` | the next occurrence in the producer's timezone: today if still ahead, else tomorrow |
| Relative | `"4h"`, `"90m"`, `"45s"`, `"2d"` | added to the ingestion instant |
| Absolute | `"2026-08-21T06:00:00-07:00"` | ISO 8601; a naive timestamp is interpreted in the producer's timezone |

Resolved deadlines MUST be in the future at ingestion; a past deadline is an error, never a silent "run now".

### 2.2 Wire form

In JSON, a resolved deadline is carried as an ISO 8601 string with offset:

```json
{ "deadline": "2026-08-21T06:00:00-07:00" }
```

Over HTTP (for gateways and proxies), the request header:

```
Offpeak-Deadline: 2026-08-21T06:00:00-07:00
```

A gateway that receives the header and cannot honor it MUST ignore it and serve the request at standard urgency, never fail it.

## 3. Scheduling semantics

- A job with a deadline MAY be executed at any time up to the deadline, on any venue that can deliver the result by then. Where it runs and when are the scheduler's choice; *whether* it lands on time is not.
- A scheduler MUST hold a **fallback path** whose completion time is reliably known (e.g. synchronous execution at list price) and MUST invoke it when the primary venue's completion becomes doubtful within a risk buffer.
- Work without a deadline is **urgent** and MUST be untouched: never delayed, never re-routed, never repriced.

## 4. Outcomes

Each job settles in exactly one terminal state:

| State | Meaning |
| --- | --- |
| `succeeded` | completed on the chosen deferred venue, by the deadline |
| `fell_back` | completed by the deadline via the fallback path (SLA met, spread not captured) |
| `failed` | not completed by the deadline, or errored |

`sla_met` is true iff the result was available at or before the deadline.

## 5. The receipt

A settlement receipt makes the trade auditable. Per job, a receipt SHOULD carry: venue, model, submission and completion instants, the resolved deadline, token counts, cost paid, equivalent list cost, and the captured spread. Costs MUST be computed against citable public price sheets; where no price is known, the receipt carries `null`, never an estimate presented as fact.

Future fields (reserved, optional): `energy_wh`, `co2e_g`, `grid_intensity_gco2_kwh` — measured or cited, labeled as such.

## 6. Conformance

A **producer** conforms if every deadline it emits resolves per §2. A **scheduler** conforms if it honors §3 and settles §4 states truthfully. A **venue driver** conforms if it reports completion and token usage accurately and supports best-effort cancellation.

---

*Maintained at [github.com/offpeak-ai/offpeak](https://github.com/offpeak-ai/offpeak). Apache-2.0.*
