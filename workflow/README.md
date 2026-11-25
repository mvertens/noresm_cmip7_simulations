# Overview

> **The experiment name links the sheet to the simulations.** The tracker's
> rows come from the sheet's `experiment_id`. A simulation's experiment comes
> from the directory its run script is in on GitHub,
> `scripts/<MIP>/<experiment>/`. The simulation goes into a row only if the
> directory's name and the `experiment_id` are spelled exactly the same,
> including capitals.

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
     the case's experiment, e.g. `historical`, which decides the case's row in
     the sheet. The directory's name must match the sheet's `experiment_id`
     exactly.
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
     counts the years done from it. It must match the sheet's `start_year`.

   The compset, the user mods and the run length in the script are not read.

3. **Case → progress.** With the case name, the tracker finds the case on NIRD
   and reads how far it has run, from its history files. The CMORization
   status comes from the output directories.

4. **Case → row in the sheet.** The rows come from the sheet alone: each
   `experiment_id` at each of its resolutions is a row, with its cost estimate,
   before any run script or case exists. The tracker then puts the case in the
   row with the same experiment and resolution, for example historical at LM.
   The script's directory and the sheet's `experiment_id` must therefore be
   spelled exactly the same, including capitals. A case whose directory matches
   no `experiment_id` has no row, and is listed under "Not shown" below the
   tracker.

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

- **`experiment_id`**: the name of the experiment. It names the experiment's
  rows and chooses their cost estimate. A run script's directory must have
  this name for its case to go into one of these rows.
- **`Resolution`**: which resolutions it is run at (LM, MM). A row with this
  left empty is not shown.
- **`Ensemble Members`**: how many members are planned
- **`min_yrs_per_sim`**: how many years each member should run
- **`start_year`**: the first model year. It must match `RUN_STARTDATE` in
  the run script. If they differ, the tracker shows a start date error and
  counts the years done from `RUN_STARTDATE`.
- **`branches from`**: the parent experiment. It must match the experiment of
  the case that `RUN_REFCASE` in the run script names. Once a run script names
  a parent, the tracker shows that parent.
- **`activity_id`, `Tier`**: which MIP the run is listed under
- **`required_model_components`**: only to estimate the cost per simulated
  year

The compsets in the sheet are not read.

The details of how the code works are in
[Technical_Description.md](Technical_Description.md).
