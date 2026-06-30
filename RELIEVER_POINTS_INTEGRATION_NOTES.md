# Reliever Points feature — integration notes

## What this adds

1 reliever point = 45 minutes. Every 8 points (6 hours) automatically earns
1 LOCAL service credit. Leftover points carry over toward the next credit —
nothing is ever lost or rounded away.

## Files included

| File | Status | What changed |
|---|---|---|
| `form6_reliever.py` | **New** | All the conversion logic lives here: logging points, computing how many credits are owed, auto-issuing them. |
| `form6_store.py` | Modified | New `reliever_points_entries` table in the schema; new `insert_reliever_points_entry()` and `load_reliever_points()` functions. |
| `form6_settings.py` | Modified | Two new settings: `reliever.minutes_per_point` (default 45) and `reliever.points_per_credit` (default 8), with get/save helpers following the same pattern as the biometrics settings. |
| `form6_actions.py` | Modified | New `delete_reliever_entry_by_id()`. |
| `tab_employees.py` | Modified | New "Reliever Points" sub-tab under **Encode Records** (where points get logged), and a new "Reliever Points" sub-tab under **Ledgers** (where admins can review/delete sessions). |
| `employee_portal.py` | Modified | New "My Reliever Points" section, shown only to employees who have at least one logged session. |

## One thing you'll need to do yourself: `app.py`

I don't have your `app.py`, so I couldn't check it directly — but based on how
`tab_employees.py`'s `render()` is called, you likely don't need to change
anything there. The new Reliever Points tabs pull their own data directly via
`load_reliever_points()` rather than expecting it passed in through `render()`'s
parameters, specifically so this wouldn't require touching `app.py`'s call site.

The one thing PyInstaller needs: if your `.spec` file or build command lists
hidden imports explicitly (rather than relying on automatic discovery), add
`form6_reliever` to that list. If your existing modules like `form6_attendance`
or `form6_biometrics` already get picked up automatically, `form6_reliever`
will too — same import pattern.

## How the carry-over works (in case you want to verify the math yourself)

Rather than keeping a separate "points remaining" counter that could drift out
of sync, the conversion always recomputes from the **full cumulative point
history**:

```
total_credits_due = floor(total_points_ever_logged / points_per_credit)
```

Each time new points are logged, it checks how many credits have already been
auto-issued (by counting `service_credit_entries` rows tagged
`source_kind='reliever'`) and creates only the difference. So:

- 5 points logged → 0 credits, 5 carried over
- +3 points (8 total) → 1 credit issued, 0 carried over
- +10 points (18 total) → 1 more credit issued (2 total), 2 carried over
- +22 points (40 total) → 3 more credits issued (5 total), 0 carried over

This was verified with automated tests before delivery, including
multi-employee isolation, fractional points, and zero/negative input
rejection.

## One deliberate limitation: deleting a points session doesn't retract credit

If you delete a logged reliever session from the Ledgers tab, any LOCAL
credit that was **already** auto-issued from it stays on the ledger — it
isn't clawed back. The UI flags this in the delete button's tooltip and in
the confirmation message after deleting.

Why: the conversion is driven by the *current* point total, not a
transaction-by-transaction link between one session and one credit. Once a
credit crosses the threshold, it's just like any other manually-entered
service credit — retracting it automatically when an unrelated history edit
happens later seemed more likely to cause confusing balance swings than to
help. If a credit genuinely needs to be removed (e.g. a logged session turns
out to be a mistake that crossed a threshold it shouldn't have), delete it
directly from the **Service Credits** ledger tab — look for entries tagged as
earned from "Reliever duty" around the relevant date.

## Settings

The conversion rates (45 min/point, 8 points/credit) are stored in
`app_settings` the same way your biometrics deduction rules are, so they're
configurable without a code change if the policy ever shifts. I didn't add a
Settings UI panel for these in this pass — happy to add one (mirroring your
existing biometrics settings panel) if you'd like employees... I mean,
admins... to be able to adjust the rate themselves instead of via direct SQL.
