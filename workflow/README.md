# Overview

## How an issue is linked to a row in the tracker

Every issue points to one run script, and the tracker gets almost everything
from that script.

1. **Issue → run script.** The run script's name starts with the issue number,
   and the script is on the `CMIP7_Runscripts` branch. For example, issue #20
   has

   ```
   scripts/DECK/historical/020.historical.LM.nor30b29a.r2i1p1f1.20261113.sh
   ```

2. **What the tracker reads from the run script:**

   - **The directory it sits in**, `scripts/<MIP>/<experiment>/`. This gives
     the experiment, e.g. `historical`, which decides the case's row in the
     sheet.
   - **`create_newcase --case …`** gives the case name, e.g.
     `NHistGhgGaxg.LM.nor30b29a.020.20261113`. The tracker uses it to find the
     case on NIRD. The `LM` or `MM` in the name gives the resolution.
   - **`./xmlchange RUN_TYPE=…`** (startup, hybrid or branch) is used to show
     how the run starts.
   - **`./xmlchange RUN_REFCASE=…`** is the case the run starts from. If
     another issue's case has that name, the run is shown as branching from
     that issue.
   - **`./xmlchange RUN_REFDATE=…`** is the date in the parent the run starts
     from.
   - **`./xmlchange RUN_STARTDATE=…`** is the model start date. The tracker
     counts the years done from it. It overrides the sheet's `start_year`.

   The compset, the user mods and the run length in the script are not read.

3. **Case → progress.** With the case name, the tracker finds the case on NIRD
   and reads how far it has run, from its history files. The CMORization
   status comes from the output directories.

4. **Case → row in the sheet.** The tracker puts the case in the row with the
   same experiment and resolution, for example historical at LM. The script's
   directory and the sheet's `experiment_id` must therefore be spelled exactly
   the same.

5. **The issue's variant label**, e.g. `r2i1p1f1`, says which ensemble member
   the case is. The tracker also uses it to find the case's CMORized files.

The chain is: **issue → run script → experiment, case name and start settings
→ case on NIRD → row in the sheet**.

## What the tracker reads from the issue

- **The issue number**, to find the issue's run script.
- **The labels:**
  - the variant, e.g. `r2i1p1f1`: which ensemble member the case is. A case
    without one is not shown in a row.
  - `completed`: the run has finished.
  - `esgf`: its data is published on ESGF.
  - `test`: the case is not a CMIP7 run, and the issue is left out.
- **The assignees**, who are shown as the case's owners. If nobody is
  assigned, the owner is the person who opened the issue.
- **The title**, only to tell real cases from notes. An issue whose title has
  a space or other characters a case name can't have is a note, and is left
  out. The title is used as the case name only if the run script gives none.

The issue's description and comments are not read.

## What the tracker reads from the sheet

- **`experiment_id`**: the name of the experiment
- **`Resolution`**: which resolutions it is run at (LM, MM). A row with this
  left empty is not shown.
- **`Ensemble Members`**: how many members are planned
- **`min_yrs_per_sim`**: how many years each member should run
- **`start_year`**: the first model year, unless the run script sets
  `RUN_STARTDATE`
- **`branches from`**: the parent experiment
- **`activity_id`, `Tier`**: which MIP the run is listed under
- **`required_model_components`, `Compset Alias`**: only to estimate the cost
  per simulated year

The details of how the code works are in
[Technical_Description.md](Technical_Description.md).
