# Batch report — `demo-n100-s3`

**outcome_source:** `synthetic_declared`  ·  **cases:** 100  ·  **constraint violations:** **0**

| Metric | Value |
|---|---|
| ₹ at risk | ₹116,250.00 |
| ₹ recovered (total) | ₹28,613.00 (24.6%) |
| &nbsp;&nbsp;gateway_attributed | ₹11,138.00 — *not our win* |
| &nbsp;&nbsp;agent_attributed | ₹16,277.00 |
| &nbsp;&nbsp;organic | ₹1,198.00 |
| ₹ cost (comms + ops) | ₹744.00 |
| **net agent contribution** | **₹15,533.00** |
| contacts per agent recovery | 1.57 |

## By decline class

| class | n | at risk | recovered | agent | gateway | organic | cost |
|---|---|---|---|---|---|---|---|
| card_expired | 5 | ₹2,895.00 | ₹999.00 | ₹999.00 | ₹0.00 | ₹0.00 | ₹5.25 |
| checkout_abandoned | 8 | ₹7,542.00 | ₹0.00 | ₹0.00 | ₹0.00 | ₹0.00 | ₹103.30 |
| do_not_honour | 22 | ₹52,478.00 | ₹4,593.00 | ₹1,197.00 | ₹3,197.00 | ₹199.00 | ₹230.60 |
| insufficient_funds | 30 | ₹20,670.00 | ₹10,638.00 | ₹2,697.00 | ₹7,941.00 | ₹0.00 | ₹29.75 |
| invoice_due_soon | 6 | ₹2,594.00 | ₹1,596.00 | ₹1,596.00 | ₹0.00 | ₹0.00 | ₹0.80 |
| invoice_overdue | 5 | ₹6,195.00 | ₹2,998.00 | ₹1,999.00 | ₹0.00 | ₹999.00 | ₹151.75 |
| mandate_cancelled | 6 | ₹5,394.00 | ₹698.00 | ₹698.00 | ₹0.00 | ₹0.00 | ₹109.35 |
| risk_afa_required | 5 | ₹5,195.00 | ₹4,196.00 | ₹4,196.00 | ₹0.00 | ₹0.00 | ₹3.35 |
| risk_card_expiring | 9 | ₹9,591.00 | ₹1,697.00 | ₹1,697.00 | ₹0.00 | ₹0.00 | ₹57.65 |
| risk_mandate_expiring | 3 | ₹1,697.00 | ₹1,198.00 | ₹1,198.00 | ₹0.00 | ₹0.00 | ₹2.20 |
| unknown | 1 | ₹1,999.00 | ₹0.00 | ₹0.00 | ₹0.00 | ₹0.00 | ₹50.00 |

## Detected (pre-failure) vs reactive (webhook)

| origin | n | at risk | recovered | agent-attributed | cost |
|---|---|---|---|---|---|
| detector (revenue at risk, pre-failure) | 36 | ₹32,814.00 | ₹11,685.00 | ₹10,686.00 | ₹319.05 |
| webhook (already failed) | 64 | ₹83,436.00 | ₹16,928.00 | ₹5,591.00 | ₹424.95 |

## By domain

| domain | n | at risk | recovered | agent-attributed | cost |
|---|---|---|---|---|---|
| checkout | 8 | ₹7,542.00 | ₹0.00 | ₹0.00 | ₹103.30 |
| receivables | 11 | ₹8,789.00 | ₹4,594.00 | ₹3,595.00 | ₹152.55 |
| recurring | 81 | ₹99,919.00 | ₹24,019.00 | ₹12,682.00 | ₹488.15 |

## Terminal status

| status | n |
|---|---|
| recovered | 37 |
| stopped_tiers | 35 |
| noop_closed | 14 |
| human | 13 |
| stopped_ev | 1 |

## Recovered cases by action in flight

| action | n | ₹ |
|---|---|---|
| mandate_reregister | 13 | ₹9,787.00 |
| defer_to_gateway | 12 | ₹11,138.00 |
| dunning_email | 10 | ₹5,690.00 |
| dunning_whatsapp | 2 | ₹1,998.00 |

## Provenance

- **p_recover:** prior (validated:false) from policy.escalation-path.json; replaced by measured where n >= 200
- **outcomes:** `synthetic_declared` — synthetic: generated from a DECLARED response model (parameters in the batch dir), NOT observed behaviour. Demonstrates the measurement pipeline + attribution split; numbers are not a performance claim.
- **attribution window:** 72.0h after the last agent action
- Every figure traces to `cases/*.json` and the hash-chained `audit.log`.
