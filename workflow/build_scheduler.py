"""The cost table of the NorESM3 CMIP7 tracker, and the code that builds its page.
nird_progress.py, next to this module, downloads the spreadsheet's DECK_FASTTRACK tab,
calls page_text() with it each time it runs, and writes the page as index.html next to
progress.json.

The runs on the page are the spreadsheet's experiments at each resolution its Resolution
column gives, e.g. "LM" or "LM, MM". An experiment with no resolution there is not run.
Each run's members and years come from the sheet's Ensemble Members and min_yrs_per_sim
columns. With more than one resolution among the runs, run names carry their resolution.

Each run is assigned a configuration from COSTS. A configuration whose compsets include
the run's compset at that resolution wins; shell-style wildcards are allowed
(N7*EsmGhgGaxg). Otherwise the required model components and compset decide:
  LAND                          -> land-only
  DCPP activity                 -> DCPP
  AGCM...                       -> AMIP at the resolution
  emission-driven (esm-*, *Esm*) -> ESM at the resolution
  anything else                 -> coupled at the resolution
A run whose configuration is not in COSTS is left out of the page, with a warning.
"""

import csv
import fnmatch
import io
import json
import os

# The page template, next to this script.
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scheduler_template.html")
HEADER_KEY = "experiment_id"
START_MARKER = "let DEFAULT_CONFIGS = ["
END_MARKER = "let configs = clone(DEFAULT_CONFIGS);"
NO_PARENT = {"", "-", "obs product"}

# The spreadsheet's column, L, with the resolutions each experiment is run at, e.g. "LM, MM".
# It is found by its heading, so the columns before it may change.
RESOLUTION_COLUMN = "Resolution"


def read_experiments(text):
    """The experiments in the spreadsheet's tab, one dictionary per row below the header row,
    the row with an experiment_id column, keyed by the header row's headings. Rows above it are
    notes.

    text: the CSV text of the spreadsheet's DECK_FASTTRACK tab."""
    rows = list(csv.reader(io.StringIO(text, newline="")))
    for i, row in enumerate(rows):
        if HEADER_KEY in [c.strip() for c in row]:
            header = [c.strip() for c in row]
            return [dict(zip(header, r)) for r in rows[i + 1:] if any(c.strip() for c in r)]
    raise ValueError(f"the spreadsheet has no header row with {HEADER_KEY}")


# The configurations: cost in core-hours per simulated year and throughput in
# simulated years per day, from the timing files of runs on Olivia (None where
# there is no timing yet), and the compsets that belong to each. A configuration
# can also carry betzyCostPerYear and betzyThroughput. The page replaces these
# with the values measured from the cases' timing files on NIRD, machine by machine.
# A core-hour is what the timing files call a pe-hour: CIME counts every core of each
# node a job uses, 128 per node on Betzy and 256 on Olivia. The two machines' cores
# differ, so a configuration's cost on one is not its cost on the other.
COSTS = [
    # N1850GhgGaxg piControl LM timing file (2048 PEs, 2036 active; 256 tasks per node)
    {"name": "NorESM3 LM", "resolution": "LM", "costPerYear": 2564.96, "throughput": 19.16,
     "compsets": "N1850GhgGaxg NHistGhgGaxg N2xCO2GhgGaxg N0p5xCO2GhgGaxg N1PctCO2BgcGhgGaxg N1PctCO2RadGhgGaxg"},
    # N1PctCO2GhgGaxg 1pctCO2 LM timing file
    {"name": "NorESM3 1pctCO2 LM", "resolution": "LM", "costPerYear": 2606.78, "throughput": 18.86,
     "compsets": "N1PctCO2GhgGaxg"},
    # N4xCO2GhgGaxg abrupt-4xCO2 LM timing file
    {"name": "NorESM3 4xCO2 LM", "resolution": "LM", "costPerYear": 2429.92, "throughput": 20.23,
     "compsets": "N4xCO2GhgGaxg"},
    # piControl MM timing file (older code)
    {"name": "NorESM3 MM", "resolution": "MM", "costPerYear": 5246.17, "throughput": 10.54,
     "compsets": ""},
    # esm-piControl LM timing file (13.295 s per model day)
    {"name": "NorESM3 ESM LM", "resolution": "LM", "costPerYear": 2760.72, "throughput": 17.80,
     "compsets": "N1850EsmGhgGaxg NHistEsmGhgGaxg N7*EsmGhgGaxg N10EsmGhgGaxg N10CdrEsmGhgGaxg N10ZecEsmGhgGaxg"},
    {"name": "NorESM3 ESM MM", "resolution": "MM", "costPerYear": None, "throughput": None,
     "compsets": ""},
    # amip LM timing file
    {"name": "NorESM3 AMIP LM", "resolution": "LM", "costPerYear": 1360.06, "throughput": 27.10,
     "compsets": "NF1850GhgAnthro NF4xCO2Ghg NFAMIPp4k NFAMIPpiF"},
    {"name": "NorESM3 AMIP MM", "resolution": "MM", "costPerYear": None, "throughput": None,
     "compsets": ""},
    # No timing files yet: the runs are tracked, and costed once their cases have timings.
    {"name": "NorESM3 land-only", "resolution": "LM", "costPerYear": None, "throughput": None,
     "compsets": ""},
    {"name": "NorESM3 DCPP", "resolution": "LM", "costPerYear": None, "throughput": None,
     "compsets": ""},
]


def number(v):
    """v as a float, or None if it isn't a number.

    v: a cell of the spreadsheet, such as "170" or ""."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def compset_rules(costs):
    """(resolution, pattern, configuration name) for every compset the cost table lists.

    costs: the cost table, COSTS."""
    return [(c["resolution"], pattern, c["name"]) for c in costs for pattern in c["compsets"].split()]


def config_name(exp, resolution, rules=()):
    """The name of the configuration from the cost table that the experiment is run with at
    the resolution, by the rule in this module's docstring.

    exp: one experiment, a row of the spreadsheet as read_experiments() gives it.
    resolution: the resolution, "LM" or "MM".
    rules: the compsets of the cost table, as compset_rules() gives them; empty to choose by
      the required model components and compset alone."""
    compset_alias = exp.get("Compset Alias", "").strip()
    for res, pattern, name in rules:
        if res == resolution and compset_alias and fnmatch.fnmatchcase(compset_alias, pattern):
            return name
    comps = exp.get("required_model_components", "").upper()
    compset = exp.get("Compset Alias", "")
    name = exp["experiment_id"]
    if comps.startswith("LAND"):
        return "NorESM3 land-only"
    if exp.get("activity_id", "").startswith("DCPP"):
        return "NorESM3 DCPP"
    if comps.startswith("AGCM"):
        return f"NorESM3 AMIP {resolution}"
    if name.startswith("esm-") or "Esm" in compset:
        return f"NorESM3 ESM {resolution}"
    return f"NorESM3 {resolution}"


def resolutions_of(exp):
    """The resolutions the experiment is run at, from its Resolution column: "LM, MM" is
    ["LM", "MM"], and an empty cell is none.

    exp: one experiment, a row of the spreadsheet as read_experiments() gives it."""
    return [r.strip().upper() for r in exp.get(RESOLUTION_COLUMN, "").replace("/", ",").split(",")
            if r.strip()]


def build(experiments, costs):
    """The configurations, the runs and the warnings, as (configs, sims, warnings): the page's
    DEFAULT_CONFIGS and DEFAULT_SIMS, and what was left out of them and why. Raises ValueError if
    the spreadsheet has no Resolution column.

    experiments: the spreadsheet's experiments, as read_experiments() gives them.
    costs: the cost table, COSTS."""
    configs = [{"id": f"c{i}", "name": c["name"], "costPerYear": c["costPerYear"],
                "throughput": c["throughput"], "betzyCostPerYear": c.get("betzyCostPerYear"),
                "betzyThroughput": c.get("betzyThroughput")} for i, c in enumerate(costs, 1)]
    cfg_id = {c["name"]: c["id"] for c in configs}
    rules = compset_rules(costs)
    if experiments and RESOLUTION_COLUMN not in experiments[0]:
        raise ValueError(f"the spreadsheet has no {RESOLUTION_COLUMN} column")
    resolutions = sorted({r for e in experiments for r in resolutions_of(e)})
    label = len(resolutions) > 1

    warnings = []
    sims = []
    for resolution in resolutions:
        chosen = [e for e in experiments if resolution in resolutions_of(e)]
        selected = []
        for e in chosen:
            if config_name(e, resolution, rules) in cfg_id:
                selected.append(e)
            else:
                warnings.append(f"{e['experiment_id']} ({resolution}): configuration "
                                f"{config_name(e, resolution, rules)!r} is not in the cost table; run left out")
        first = len(sims) + 1
        sim_id = {e["experiment_id"]: f"r{first + i}" for i, e in enumerate(selected)}
        for e in selected:
            name = e["experiment_id"]
            parent = e.get("branches from", "").strip()
            depends = []
            if parent.lower() not in NO_PARENT:
                if parent in sim_id:
                    depends.append(sim_id[parent])
                else:
                    warnings.append(f"{name} ({resolution}): parent {parent!r} is not selected; no dependency")
            years = number(e.get("min_yrs_per_sim"))
            if years is None:
                warnings.append(f"{name} ({resolution}): no min_yrs_per_sim; years set to 0")
            ens = number(e.get("Ensemble Members"))
            tier = e.get("Tier", "").strip()
            activity = e.get("activity_id", "").split("/")[0].strip()
            sims.append({
                "id": sim_id[name],
                "name": f"{name} {resolution}" if label else name,
                # The page matches a case to its run by its experiment, the directory of its
                # run script, and its resolution.
                # It counts years done from the cases' RUN_STARTDATE, and from the start year
                # only while no run script can be read.
                "experiment": name,
                "resolution": resolution,
                "startYear": int(number(e.get("start_year")) or 1),
                "dependsOn": depends,
                "years": int(years) if years else 0,
                "ensemble": int(ens) if ens is not None else None,
                "configId": cfg_id[config_name(e, resolution, rules)],
                "status": "pending",
                "yearsCompleted": 0,
                "steps": [],
                # The DECK experiments are grouped under their activity, CMIP.
                "mip": "CMIP" if tier == "DECK" or activity in ("", "CMIP") else activity,
                "amip": "Yes" if e.get("required_model_components", "").upper().startswith("AGCM") else "No",
            })
    return configs, sims, warnings


def script_json(x):
    """x as JSON that is safe inside the page's <script>: "</" is written "<\\/", so no
    text from the spreadsheet can end the script early.

    x: anything json.dumps can write, such as one run."""
    return json.dumps(x, ensure_ascii=False).replace("</", "<\\/")


def literal(items):
    """The items as the lines of a JavaScript array's body, one item per line.

    items: the configurations or the runs, as build() gives them."""
    return "\n".join("    " + script_json(x) + "," for x in items)


def page_text(sheet_text, sheet):
    """The page as text, with the configurations, the runs and the warnings, as
    (page, configs, sims, warnings).

    sheet_text: the CSV text of the spreadsheet's DECK_FASTTRACK tab.
    sheet: where sheet_text came from, for the line on the page: {"url": the spreadsheet's
      address, "read": when it was read}.

    1. It reads the experiments from sheet_text.
    2. For each resolution in the spreadsheet's Resolution column, it picks the experiments
       that column gives it for. Each becomes a run, e.g. "historical LM".
    3. Each run takes from the spreadsheet its members (Ensemble Members), its years per
       member (min_yrs_per_sim), its start year, the parent it branches from and its MIP
       (DECK runs are grouped under CMIP).
    4. Each run is given a configuration from COSTS: one that lists the run's compset, or
       otherwise land-only, DCPP, AMIP, ESM or coupled, by the rule in this module's
       docstring.
    5. It replaces the template's data block with the configurations, the runs, sheet and
       the cost unit. The JSON is escaped so that nothing in it can end the page's script.

    It reads and writes no files but the template: nird_progress.py reads the spreadsheet
    and writes the page next to progress.json. It knows nothing about progress: the page
    reads progress.json each time it is opened, and fills in the cases, the years done, the
    CMORization and the measured costs.

    Raises ValueError, or OSError, if the page can't be built."""
    configs, sims, warnings = build(read_experiments(sheet_text), COSTS)
    with open(TEMPLATE, encoding="utf-8") as f:
        page = f.read()
    s = page.find(START_MARKER)
    e = page.find(END_MARKER)
    if s < 0 or e < 0:
        raise ValueError(f"{TEMPLATE} has no default-data block")
    block = (START_MARKER + "\n" + literal(configs) + "\n  ];\n"
             + "  let DEFAULT_SIMS = [\n" + literal(sims) + "\n  ];\n"
             + f"  let DEFAULT_SHEET = {script_json(sheet)};\n"
             + '  let DEFAULT_COST_UNIT = "core-hr";\n\n  ')
    return page[:s] + block + page[e:], configs, sims, warnings
