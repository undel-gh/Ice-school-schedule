# Tariffs, Entitlements and Attendance Coverage

**Status:** normative domain-model amendment  
**Scope:** current tariff matrix, subscription allowances, one-time services, trial lessons, individual/mini-group lessons, skate rental  
**Applies to:** Domain Model, State Machines and Domain Events, Django Models and Application Services Specification

## 0. Normative priority

This document is the authoritative specification for tariffs, subscription allowances,
one-time lesson entitlements and attendance coverage.

Where the following documents still describe the earlier single-category subscription
model, this document takes precedence:

- `Domain Model — школа фигурного катания.md`;
- `State Machines и Domain Events.md`;
- `Django Models and Application Services Specification.md`;
- `Спецификация системы расписания, посещаемости и абонементов школы фигурного катания.md`.

In particular, the following earlier concepts are superseded:

```text
SubscriptionPlan.category
SubscriptionPlan.visit_limit
Subscription.category_snapshot
Subscription.visit_limit_snapshot
SubscriptionUsage
SubscriptionLedgerEntry.subscription_id
MakeupEntitlement.source_subscription_id
```

They are replaced by:

```text
SubscriptionPlanAllowance
SubscriptionAllowance
AttendanceCoverage
SubscriptionLedgerEntry.allowance_id
MakeupEntitlement.source_subscription_allowance_id
OneTimeEntitlement
```

## 1. Purpose

The tariff model MUST distinguish:

1. a commercial product or service offered by the school;
2. the attendance entitlement granted to a student;
3. the actual attendance recorded by the coach;
4. the financial/payment transaction, which may be implemented later.

`Attendance` remains the source of truth for actual presence. A tariff or payment record MUST NOT be treated as proof that the student actually attended.

## 2. Current tariff matrix

The current monthly subscription products are:

| Plan | ICE allowance | HALL allowance |
|---|---:|---:|
| 4 HALL | 0 | 4 |
| 8 ICE | 8 | 0 |
| 8 ICE + 8 HALL | 8 | 8 |
| 8 ICE + 12 HALL | 8 | 12 |
| 12 ICE | 12 | 0 |
| 12 ICE + 8 HALL | 12 | 8 |
| 12 ICE + 12 HALL | 12 | 12 |
| 12 ICE + 16 HALL | 12 | 16 |
| 16 ICE + 16 HALL | 16 | 16 |
| 20 ICE + 16 HALL | 20 | 16 |
| 20 ICE + 20 HALL | 20 | 20 |
| 20 ICE + 24 HALL | 20 | 24 |
| 24 ICE + 24 HALL | 24 | 24 |

All current subscription plans have a validity period of one month.

The current mapping of lesson types to subscription categories remains:

- `ICE` lesson -> `ICE` allowance;
- `PHYSICAL` lesson -> `HALL` allowance;
- `CHOREOGRAPHY` lesson -> `HALL` allowance.

A mixed subscription is ONE subscription product with several independent allowances. It MUST NOT be represented as two unrelated subscriptions.

Example:

```text
Subscription
"8 ICE + 12 HALL"
01.10–31.10

├── SubscriptionAllowance ICE
│   └── initial grant = 8
│
└── SubscriptionAllowance HALL
    └── initial grant = 12
```

## 3. Required replacement of the previous subscription model

The following previous fields are obsolete and MUST be removed from the normative model:

```text
SubscriptionPlan.category
SubscriptionPlan.visit_limit

Subscription.category_snapshot
Subscription.visit_limit_snapshot
```

They are replaced by allowance collections.

### 3.1 SubscriptionPlan

```text
SubscriptionPlan
    id
    code
    name
    validity_months = 1
    is_active
    created_at
    updated_at
```

A plan describes the whole commercial subscription product.

### 3.2 SubscriptionPlanAllowance

```text
SubscriptionPlanAllowance
    id
    plan_id
    category: ICE | HALL
    visit_limit
```

Required invariant:

```text
UNIQUE(plan_id, category)
visit_limit > 0
```

A plan may currently contain one or two rows.

Examples:

```text
"8 ICE"

ICE = 8
```

```text
"8 ICE + 12 HALL"

ICE  = 8
HALL = 12
```

### 3.3 Subscription

```text
Subscription
    id
    student_id
    plan_id

    plan_code_snapshot
    plan_name_snapshot

    valid_from
    valid_until

    created_at
    created_by

    cancelled_at
    cancelled_by
```

The concrete dates are authoritative. `validity_months` is only a plan default/policy input used when a subscription is issued.

### 3.4 SubscriptionAllowance

When a subscription is issued, every `SubscriptionPlanAllowance` MUST be snapshotted into a concrete `SubscriptionAllowance`.

```text
SubscriptionAllowance
    id
    subscription_id
    category: ICE | HALL
    visit_limit_snapshot
```

Required invariant:

```text
UNIQUE(subscription_id, category)
visit_limit_snapshot > 0
```

Example:

```text
Subscription #123
plan snapshot = "12 ICE + 16 HALL"

SubscriptionAllowance #A
category = ICE
visit_limit_snapshot = 12

SubscriptionAllowance #B
category = HALL
visit_limit_snapshot = 16
```

Changing the plan later MUST NOT change existing subscriptions.

## 4. Subscription ledger

The ledger belongs to a concrete allowance, not to the subscription as a whole.

Replace:

```text
SubscriptionLedgerEntry.subscription_id
```

with:

```text
SubscriptionLedgerEntry.allowance_id
```

Normative model:

```text
SubscriptionLedgerEntry
    id
    allowance_id

    entry_type:
        GRANT
        CONSUME
        RESTORE
        ADJUSTMENT

    delta

    attendance_coverage_id nullable

    reason
    created_at
    created_by
```

The authoritative balance of a category is:

```text
SUM(SubscriptionLedgerEntry.delta)
WHERE allowance_id = ...
```

There is no common "subscription balance" for a mixed plan. The UI MUST expose separate balances.

Example:

```text
8 ICE + 12 HALL

ICE:
    used: 3
    remaining: 5

HALL:
    used: 7
    remaining: 5
```

Issuing this subscription creates TWO initial ledger entries:

```text
ICE allowance:
GRANT +8

HALL allowance:
GRANT +12
```

## 5. Attendance coverage

The name `SubscriptionUsage` is too narrow because an actual visit can also be covered by a one-time or trial entitlement.

The normative entity SHOULD therefore be renamed to:

```text
AttendanceCoverage
```

It answers one question:

> What entitlement covered this concrete `Attendance=PRESENT`?

Suggested model:

```text
AttendanceCoverage
    id
    attendance_id

    subscription_allowance_id nullable
    one_time_entitlement_id nullable

    makeup_entitlement_id nullable

    consume_ledger_entry_id nullable

    created_at
    created_by

    reversed_at nullable
    reversed_by nullable
```

Exactly one primary coverage source MUST be set:

```text
subscription_allowance_id XOR one_time_entitlement_id
```

`makeup_entitlement_id` is not a separate paid visit. It is a modifier that allows use of a referenced subscription allowance outside its ordinary validity rules.

Required invariants:

```text
one active AttendanceCoverage per Attendance
one active use per one-time entitlement
one active use per MakeupEntitlement
```

When the primary source is a `SubscriptionAllowance`, the category MUST equal the lesson's `subscription_category`.

## 6. Current non-subscription services

The school currently also offers:

1. one-time ICE lesson;
2. one-time HALL lesson;
3. individual ICE lesson;
4. mini-group ICE lesson;
5. trial ICE lesson at 50% of the ordinary ICE lesson price;
6. skate rental.

There is currently no trial HALL lesson.

These services MUST NOT be modeled as monthly subscriptions with an artificial limit of one visit.

## 7. OneTimeEntitlement

A paid/admitted one-time visit is represented separately.

```text
OneTimeEntitlement
    id
    student_id
    lesson_id

    entitlement_type:
        SINGLE_ICE
        SINGLE_HALL
        INDIVIDUAL_ICE
        MINI_GROUP_ICE
        TRIAL_ICE

    category:
        ICE
        HALL

    created_at
    created_by

    cancelled_at nullable
    cancelled_by nullable
```

For the current business processes the entitlement SHOULD be bound to a concrete `Lesson`.

This prevents an ordinary one-time payment from being accidentally consumed for an unrelated later lesson.

Required mapping:

```text
SINGLE_ICE      -> ICE
SINGLE_HALL     -> HALL
INDIVIDUAL_ICE  -> ICE
MINI_GROUP_ICE  -> ICE
TRIAL_ICE       -> ICE
```

No `TRIAL_HALL` type exists.

State is derived:

```text
AVAILABLE
USED
CANCELLED
```

`USED` means that an active `AttendanceCoverage` references this entitlement.

The system MUST NOT create consumption until the coach records:

```text
Attendance = PRESENT
```

If the student does not attend, the entitlement remains unused unless a separate cancellation/refund policy says otherwise.

## 8. Coverage-selection order

When `Attendance` becomes `PRESENT`, the coverage service MUST use explicit lesson-bound entitlements before consuming an ordinary monthly allowance.

Recommended deterministic order:

```text
1. OneTimeEntitlement bound to this exact Lesson
2. target-specific MakeupEntitlement
3. other valid MakeupEntitlement, earliest expiry first
4. ordinary active SubscriptionAllowance, earliest parent Subscription expiry first
5. UNCOVERED
```

This prevents:

- a trial lesson from consuming the student's monthly subscription;
- an individual lesson from consuming a normal group allowance;
- a separately paid one-time lesson from decrementing a subscription by mistake.

The operation remains idempotent.

## 9. Makeup and medical transfer

The previously defined `MakeupEntitlement` remains valid.

Its source changes from:

```text
source_subscription_id
```

to:

```text
source_subscription_allowance_id
```

because a make-up always belongs to one category allowance.

Example:

```text
medical absence from an ICE lesson
    ↓
MakeupEntitlement
    ↓
source ICE allowance
```

A medical or administrative make-up MUST NOT increase the purchased quantity.

It only permits consumption of an existing positive allowance balance under exceptional date/target rules.

## 10. Ordinary make-up with another group

An ordinary make-up without a medical extension remains:

```text
LessonEnrollment(reason=MAKEUP)
```

It creates no extra visit.

When the coach marks the student `PRESENT` in the other group, the usual category allowance is consumed.

Example:

```text
student misses ICE in Group A
(no consumption)

student attends ICE in Group B
(PRESENT)

ICE allowance:
-1
```

## 11. Product/service catalog for future billing

Payment itself may remain outside the MVP, but the architecture MUST reserve a clean catalog boundary.

A future `ServiceProduct`/catalog may contain at least:

```text
SUBSCRIPTION_PLAN
SINGLE_ICE
SINGLE_HALL
INDIVIDUAL_ICE
MINI_GROUP_ICE
TRIAL_ICE
STUDENT_ICE_ADMISSION
COACH_ICE_ADMISSION
SKATE_RENTAL
```

Actual money values are not specified by this document.

The catalog MUST be separate from `Attendance` and from `SubscriptionAllowance`.

## 12. Individual lesson price composition

Current business rule:

```text
individual lesson total
=
individual lesson price
+ coach ice admission
+ student ice admission
```

The future billing model SHOULD represent these as separate charge/order lines:

```text
Order / Charge

├── INDIVIDUAL_ICE
├── COACH_ICE_ADMISSION
└── STUDENT_ICE_ADMISSION
```

They MUST NOT be collapsed into attendance counters.

Only `INDIVIDUAL_ICE` grants the attendance entitlement.

The admission charge lines are financial components and do not grant extra visits.

## 13. Mini-group price composition

Current business rule is applied per participant:

```text
for each participant:

mini-group lesson price
+ student ice admission
+ coach ice admission
```

The future charge model SHOULD therefore support participant-scoped charge lines.

For each participant:

```text
├── MINI_GROUP_ICE
├── STUDENT_ICE_ADMISSION
└── COACH_ICE_ADMISSION
```

`MINI_GROUP_ICE` creates/binds the participant's `OneTimeEntitlement`.

The admission components do not create additional attendance rights.

The current rule is documented literally; no automatic cost sharing of the coach admission between participants is implied.

## 14. Trial lesson

Current rule:

```text
TRIAL_ICE price = 50% of ordinary SINGLE_ICE price
```

There is no trial HALL product.

The 50% rule belongs to pricing/catalog logic, not to attendance logic.

Attendance coverage uses:

```text
OneTimeEntitlement(type=TRIAL_ICE)
```

No assumption is made here about how many trial lessons one student may purchase; that remains a separate business rule until explicitly defined.

## 15. Skate rental

Skate rental is an add-on service.

It:

- does not grant access to a lesson;
- does not consume ICE/HALL allowances;
- does not change Attendance;
- may later be represented as a separate order/charge item connected to the student and optionally to a lesson.

```text
SKATE_RENTAL
```

MUST NOT be modeled as an entitlement.

## 16. Revised aggregate overview

```text
SubscriptionPlan
    │
    └── SubscriptionPlanAllowance [1..2]
                │
                │ snapshot on issue
                ▼
Subscription
    │
    └── SubscriptionAllowance [1..2]
                │
                └── SubscriptionLedgerEntry
                           ▲
                           │
Attendance ── AttendanceCoverage
                   │
                   ├── SubscriptionAllowance
                   │      └── optional MakeupEntitlement
                   │
                   └── OneTimeEntitlement
```

Future billing remains a separate bounded context:

```text
Catalog / Pricing
        │
        ▼
Order / Charge
   └── ChargeItem(s)
        │
        ├── may create Subscription
        ├── may create OneTimeEntitlement
        └── may be an add-on only
```

## 17. Revised source-of-truth table

| Question | Source of truth |
|---|---|
| Did the student actually attend? | `Attendance` |
| What did a monthly plan include? | `SubscriptionPlanAllowance` |
| What did this issued subscription include? | `SubscriptionAllowance` |
| How many ICE/HALL visits remain? | sum of ledger entries for the corresponding `SubscriptionAllowance` |
| What covered this actual visit? | active `AttendanceCoverage` |
| Was this visit an ordinary one-time/trial/individual/mini-group visit? | `OneTimeEntitlement` referenced by `AttendanceCoverage` |
| Was an expired allowance exceptionally usable? | `MakeupEntitlement` |
| What was actually paid/refunded? | future billing/payment subsystem, not Attendance |

## 18. Django model changes

### Remove from SubscriptionPlan

```text
category
visit_limit
```

### Add

```text
SubscriptionPlanAllowance
```

with:

```text
FK plan -> SubscriptionPlan
category ICE/HALL
visit_limit > 0
UNIQUE(plan, category)
```

### Remove from Subscription

```text
category_snapshot
visit_limit_snapshot
```

### Add

```text
SubscriptionAllowance
```

with:

```text
FK subscription -> Subscription
category ICE/HALL
visit_limit_snapshot > 0
UNIQUE(subscription, category)
```

### Change SubscriptionLedgerEntry

Replace:

```text
FK subscription
```

with:

```text
FK allowance -> SubscriptionAllowance
```

### Replace SubscriptionUsage

Rename/replace with:

```text
AttendanceCoverage
```

supporting either:

```text
subscription_allowance
```

or:

```text
one_time_entitlement
```

and optional:

```text
makeup_entitlement
```

### Add

```text
OneTimeEntitlement
```

for:

```text
SINGLE_ICE
SINGLE_HALL
INDIVIDUAL_ICE
MINI_GROUP_ICE
TRIAL_ICE
```

## 19. Application-service changes

Replace:

```text
issue_subscription()
```

implementation so that it:

```text
1. creates Subscription;
2. copies every SubscriptionPlanAllowance;
3. creates one SubscriptionAllowance per category;
4. creates one GRANT entry per allowance.
```

Replace:

```text
subscription_balance(subscription_id)
```

with:

```text
allowance_balance(allowance_id)
```

and optionally:

```text
subscription_balances(subscription_id)
    -> {ICE: ..., HALL: ...}
```

Replace internal coverage lookup so that:

```text
assign_attendance_coverage()
```

supports:

```text
OneTimeEntitlement
MakeupEntitlement + SubscriptionAllowance
ordinary SubscriptionAllowance
```

`reverse_attendance_coverage()` MUST:

- create `RESTORE +1` only for coverage backed by a subscription allowance;
- release a used one-time entitlement when its coverage is reversed;
- return a used MakeupEntitlement to available state when applicable.

## 20. Required database invariants

At minimum:

```text
UNIQUE(SubscriptionPlanAllowance.plan, category)

UNIQUE(SubscriptionAllowance.subscription, category)

SubscriptionPlanAllowance.visit_limit > 0

SubscriptionAllowance.visit_limit_snapshot > 0

one active AttendanceCoverage per Attendance

one active AttendanceCoverage per OneTimeEntitlement

one active AttendanceCoverage per MakeupEntitlement
```

For `AttendanceCoverage`, database checks SHOULD enforce:

```text
exactly one of:
    subscription_allowance
    one_time_entitlement
```

If `makeup_entitlement IS NOT NULL` then:

```text
subscription_allowance IS NOT NULL
```

and the entitlement's source allowance MUST match the selected allowance at service level.

## 21. Required tests

Add or update tests for:

1. issuing ICE-only plan;
2. issuing HALL-only plan;
3. issuing mixed plan;
4. independent ICE/HALL balances;
5. ICE attendance cannot consume HALL allowance;
6. HALL attendance cannot consume ICE allowance;
7. mixed plan can be exhausted independently by category;
8. one-time ICE attendance does not consume monthly ICE allowance;
9. one-time HALL attendance does not consume monthly HALL allowance;
10. trial ICE coverage does not consume subscription;
11. trial HALL type does not exist;
12. individual lesson coverage uses explicit one-time entitlement;
13. mini-group participant coverage uses explicit one-time entitlement;
14. skate rental has no attendance entitlement;
15. medical make-up references the correct source allowance;
16. reversal restores only the allowance actually consumed;
17. concurrent consumption cannot drive an allowance below zero;
18. plan edits do not change issued allowance snapshots.

## 22. Migration note

Before production data exists, the preferred implementation is to update the initial schema directly to the allowance-based model.

If migration is required after data exists, convert each legacy subscription:

```text
legacy category_snapshot + visit_limit_snapshot
```

into one `SubscriptionAllowance` row and re-point its ledger entries to that allowance.

Mixed plans MUST be created natively as two allowances; they SHOULD NOT be synthesized as two unrelated legacy subscriptions.

## 23. Architectural rule

The system MUST keep these concerns separate:

```text
PRICE
What does the school charge?

ENTITLEMENT
What visits is the student allowed to use?

ATTENDANCE
What did the coach confirm actually happened?

PAYMENT
What money was paid or refunded?
```

A change in price or refund policy MUST NOT rewrite Attendance history.

A correction to Attendance MUST NOT rewrite historical price definitions.

This separation is required for reliable subscription accounting, dispute resolution and future payment/refund support.
