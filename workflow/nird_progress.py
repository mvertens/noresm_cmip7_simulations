"""Write progress.json for the tracker from the GitHub issues and the files on NIRD, and
build the tracker page, index.html, next to it.

workflow/Technical_Description.md describes what it reads and records.
"""

import argparse
import datetime
import fnmatch
import glob
import gzip
import http.client
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

# The page is built by build_scheduler.py, next to this script. python3 -I leaves this
# script's directory off the import path, so it is added here.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_scheduler  # noqa: E402

REPO = "NorESMhub/noresm_cmip7_simulations"

# The NorESM3 CMIP7 experiments spreadsheet and its DECK_FASTTRACK tab, which the page's runs
# come from. It is read on every run, so it must stay readable by anyone with the link.
SHEET = "https://docs.google.com/spreadsheets/d/1PZQHmOHVENJeCqwSfk4wfp8okQXw60Bv"
SHEET_TAB = "13331701"


class Failed(Exception):
    """Something the tracker needs could not be read or written, so neither progress.json nor
    the page is updated. copied is whether the validation report copies had already been
    updated by then."""

    def __init__(self, message, copied=False):
        super().__init__(message)
        self.copied = copied


# Where things are on NIRD. {case} is a case's name; {resolution}, {experiment} and
# {variant} are its simulation's.
PROGRESS = "/nird/datalake/NS9560K/www/diagnostics/noresm/cmip7_planning/progress.json"
CASE_DIR = "/nird/datalake/NS9560K/noresm3/cases/{case}"

# A case's model date comes from the dated files that fit HIST_PATTERN in HIST_DIR.
HIST_DIR = CASE_DIR + "/atm/hist"
HIST_PATTERN = "*.cam.i.*.nc"

# Each tool's CMORized output: cmip7-prep's for each case, and cmor4cmip7's for each
# simulation, shared by all its cases. cmip7-prep's is in its own directory, on datapeak or
# on datalake; the first that exists is used.
CMIP7PREP_DIRS = ("/nird/datapeak/NS9560K/noresm3/cmip7prep/{case}",
                  "/nird/datalake/NS9560K/noresm3/cmip7prep/{case}")
CMOR4CMIP7_DIR = "/nird/datapeak/NS9034K/.CMIP7/NorESM3-{resolution}/{experiment}/{variant}"

# cmip7-prep's validation report, below its output directory, and where the tracker keeps a
# copy of each case's, below the directory of progress.json, so the page can link to it.
VALIDATION_REPORT = "cmor/validation_reports"
VALIDATION_COPIES = "validation"

# The characters an issue's title may have.
TITLE_CHARACTERS = re.compile(r"[A-Za-z0-9._-]+")

# compset.resolution.version...: NHistGhgGaxg.LM.nor30b29a.r2i1p1f1.020.20261113, or without
# the variant, N2xCO2GhgGaxg.LM.nor30b28.014.20261106. The beta may end in a letter: nor30b29a.
# The compset's case is not checked, so the older names with a small n, such as
# n1850GHGGaxg.LM.nor30b28.011.20261102, are read too.
TITLE = re.compile(r"^[A-Za-z0-9-]+\.(LM|MM)\.nor30(b\d+[a-z]?)\.")
RESOLUTION = {"LM": "LM", "MM": "MM"}

# YYYY-MM, YYYY-MM-DD or YYYY-MM-DD-SSSSS just before .nc
DATE = re.compile(r"\.(\d{4})-(\d{2})(?:-(\d{2})(?:-\d{5})?)?\.nc$")

# cmip7-prep's record of the steps that finished for one set of years.
COMPLETED = re.compile(r"completed_steps_(\d+)-(\d+)\.json$")

# The directory of one set of years' step logs, in cmip7-prep's logs/: 101-150. cmip7-prep
# makes it when it starts the set, before any step has finished.
LOG_YEARS = re.compile(r"(\d+)-(\d+)")

# How long a running step's log may go unchanged before the tracker says the run may have
# stopped. A step can write nothing for most of an hour, e.g. while it makes one batch of
# daily time series.
STALE_LOG = datetime.timedelta(hours=2)

# A CMOR file's variable and the years of its time range, before .nc:
# so_Omon_NorESM3_piControl_r1i1p1f1_gn_000101-001012.nc
CMOR_FILE = re.compile(r"^(.+)_(\d{4})\d*-(\d{4})\d*\.nc$")

# A kind of output measured over at least this many years is not measured again.
STORAGE_YEARS = 5

# Sizes are in binary units, as NIRD's quotas are: GiB here, and TiB on the page.
BYTES_PER_GIB = 2**30

# A restart set's directory in rest/, named after its date: 0101-01-01-00000.
REST_SET = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-\d{5}$")

# Finds the dates in a file's name, so that file_years() can tell which model years the
# file covers. Only the year of each date is kept. A date is written in one of these
# forms: YYYY, YYYYMM, YYYYMMDD, YYYY-MM, YYYY-MM-DD or YYYY-MM-DD-SSSSS. Digits that are
# part of a longer number are not a date. For example:
#   N1850GhgGaxg.cam.h0a.0001-01.nc has one date, 0001-01, so it covers year 1.
#   so_Omon_NorESM3_piControl_r1i1p1f1_gn_000101-001012.nc has two, 000101 and 001012,
#   so it covers years 1 to 10.
NAME_DATE = re.compile(r"(?<!\d)(\d{4})(?:\d{2}|\d{4})?(?:-\d{2}(?:-\d{2}(?:-\d{5})?)?)?(?!\d)")

# The CMORization issues, each naming the cases it CMORizes, and the CMOR tag version in its
# description, on the line after its label: "**cmor tag version (decided by ...)**\n20261109".
CMOR_REPO = "NorESMhub/CMIP7_CMOR-Publish"
CMOR_TAG_VERSION = re.compile(r"cmor[ \t]+tag[ \t]+version[^\d]{0,300}?\bv?(\d{8})\b", re.I)

# The run scripts are on this branch, below scripts/, each named after its case's issue:
# DECK/piControl/011.piControl.LM.beta28.r1i1p1f1.20261102.sh is #11's.
RUNSCRIPTS_BRANCH = "CMIP7_Runscripts"
SCRIPT_ISSUE = re.compile(r"^0*(\d+)\..*\.sh$")

# The settings read from a run script's ./xmlchange lines, and the names they are written under.
RUN_SETTINGS = {"RUN_TYPE": "type", "RUN_REFCASE": "refcase", "RUN_REFDATE": "refdate",
                "RUN_STARTDATE": "startdate"}

# A shell variable given a plain value: refcase="NHistGhgGaxg.LM.nor30b29a.r2i1p1f1.020.20261113"
SHELL_VARIABLE = re.compile(r"""^(?:export\s+)?([A-Za-z_]\w*)=("[^"$`]*"|'[^']*'|[^\s"'$`;]*)\s*(?:#.*)?$""")

# What separates one shell command from the next on a line.
COMMAND_SEPARATORS = {";", "&&", "||", "|", "&"}

# The version directory a CMOR file sits under: .../r1i1p1f1/v20261028/so_..._000101-001012.nc
VERSION_DIR = re.compile(r"^v\d{8}$")
VARIANT = re.compile(r"^r\d+i\d+p\d+f\d+$")

# Lines of a timing file, one per job: noresm_timing.<case>.<job id>.<date>.
TIMING_LINES = {
    "cost": re.compile(r"^\s*Model Cost:\s*([\d.]+)\s+pe-hrs/simulated_year", re.M),
    "sypd": re.compile(r"^\s*Model Throughput:\s*([\d.]+)\s+simulated_years/day", re.M),
    "days": re.compile(r"^\s*run length\s*:\s*([\d.]+)\s+days", re.M),
    "pes": re.compile(r"^\s*pe count for cost estimate\s*:\s*(\d+)", re.M),
    "machine": re.compile(r"^\s*Machine\s*:\s*(\S+)", re.M),
    "date": re.compile(r"^\s*Curr Date\s*:\s*(.+?)\s*$", re.M),
}


def github_api(url):
    """The JSON GitHub's API returns for url. GitHub allows 60 anonymous requests an hour
    from one address, which every user on NIRD shares; once they are used up, this raises
    Failed with the time they are available again.

    url: the address of a GitHub API request, such as
      https://api.github.com/repos/NorESMhub/noresm_cmip7_simulations/issues?page=1."""
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "nird_progress"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (403, 429) and e.headers.get("X-RateLimit-Remaining") == "0":
            reset = e.headers.get("X-RateLimit-Reset")
            when = (datetime.datetime.fromtimestamp(int(reset)).astimezone().strftime("%H:%M %Z")
                    if reset and reset.isdigit() else "within the hour")
            raise Failed("GitHub's limit of 60 requests an hour from NIRD has been used up, by this "
                         f"script or by other users on NIRD. Run the script again after {when}.") from e
        raise


def fetch_sheet():
    """The spreadsheet's DECK_FASTTRACK tab as CSV text. It takes no arguments: the tab is
    SHEET_TAB of SHEET. Raises Failed if it can't be read, including when Google sends a sign-in
    page because the spreadsheet is no longer shared."""
    url = f"{SHEET}/export?format=csv&gid={SHEET_TAB}"
    req = urllib.request.Request(url, headers={"User-Agent": "nird_progress"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            kind = r.headers.get_content_type()
            text = r.read().decode("utf-8")
    except (OSError, ValueError, http.client.HTTPException) as e:
        raise Failed(f"The spreadsheet could not be read: {getattr(e, 'reason', None) or e}") from e
    if kind != "text/csv":
        raise Failed(f"The spreadsheet came back as {kind}, not CSV: it may no longer be readable "
                     "by anyone with the link.")
    return text


def check_issues(issues, source):
    """Raises Failed unless issues is a list of issues as GitHub's API gives them, each with
    a number, a title, labels and a user. Anything else, such as an error message GitHub sent
    instead, would otherwise give a page with wrong or missing runs.

    issues: what GitHub's API gave as one page of a repository's issues.
    source: where it was read from, for the message, such as "GitHub's page 2 of
      NorESMhub/CMIP7_CMOR-Publish's issues"."""
    if not isinstance(issues, list):
        raise Failed(f"{source} did not give a list of issues: {str(issues)[:200]}")
    for i in issues:
        ok = (isinstance(i, dict) and isinstance(i.get("number"), int) and isinstance(i.get("title"), str)
              and isinstance(i.get("labels"), list) and isinstance(i.get("user"), dict))
        if not ok:
            raise Failed(f"{source} gave something that is not an issue: {str(i)[:200]}")


def fetch_issues(repo):
    """Every issue in the repository, open and closed, without pull requests. Raises Failed
    if GitHub gives anything but issues.

    repo: the repository as owner/name, such as NorESMhub/CMIP7_CMOR-Publish."""
    issues = []
    page = 1
    while True:
        batch = github_api(f"https://api.github.com/repos/{repo}/issues?state=all&per_page=100&page={page}")
        check_issues(batch, f"GitHub's page {page} of {repo}'s issues")
        issues.extend(i for i in batch if "pull_request" not in i)
        # A page with fewer than a hundred is the last, so no empty page is fetched after it.
        if len(batch) < 100:
            return issues
        page += 1


def list_run_scripts(repo):
    """The run scripts' paths below scripts/ on GitHub, as (paths, None), or (None, why) if
    they couldn't be listed.

    repo: the repository, as owner/name, whose CMIP7_Runscripts branch has the run scripts."""
    try:
        tree = github_api(f"https://api.github.com/repos/{repo}/git/trees/{RUNSCRIPTS_BRANCH}?recursive=1")["tree"]
        return [t["path"][len("scripts/"):] for t in tree
                if t["type"] == "blob" and t["path"].startswith("scripts/") and t["path"].endswith(".sh")], None
    except (OSError, ValueError, KeyError, http.client.HTTPException) as e:
        return None, str(getattr(e, "reason", None) or e)


def read_run_script(repo, path):
    """The text of one run script on GitHub, as (text, None), or (None, why) if it couldn't
    be read.

    repo: the repository, as owner/name, whose CMIP7_Runscripts branch has the run script.
    path: the run script's path below scripts/, as list_run_scripts() gives it, such as
      CMIP/piControl/011.piControl.LM.beta28.r1i1p1f1.20261102.sh."""
    try:
        url = (f"https://raw.githubusercontent.com/{repo}/{RUNSCRIPTS_BRANCH}/scripts/"
               + urllib.parse.quote(path))
        req = urllib.request.Request(url, headers={"User-Agent": "nird_progress"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", errors="replace"), None
    except urllib.error.HTTPError as e:
        return None, f"not found at scripts/{path}" if e.code == 404 else f"not read, HTTP {e.code}"
    except (OSError, http.client.HTTPException) as e:
        return None, str(getattr(e, "reason", None) or e)


def xmlchange_calls(line):
    """The arguments of each call to xmlchange on the line, as a list of lists of strings. A
    call is any command whose program is named xmlchange, whatever its path: ./xmlchange,
    $CASEROOT/xmlchange, or one after other commands, as in cd $CASEROOT && ./xmlchange.
    A line the shell could not split, such as one with an unclosed quote, has none.

    line: one line of a shell script, with any lines continued by a backslash joined to it."""
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return []
    calls = []
    command = []
    for token in tokens + [";"]:
        if token in COMMAND_SEPARATORS:
            if command and os.path.basename(command[0]) == "xmlchange":
                calls.append(command[1:])
            command = []
        else:
            command.append(token)
    return calls


def run_settings(text):
    """The RUN_TYPE, RUN_REFCASE, RUN_REFDATE and RUN_STARTDATE a run script sets with
    xmlchange, as a dict with the keys type, refcase, refdate and startdate, for those it sets.
    They are set either as KEY=value[,KEY=value] or as --id KEY --val value. A shell variable
    the script gives a plain value, such as refcase="...", is filled in; a setting whose value
    still depends on the shell, such as $(date), is left out.

    text: the run script's text."""
    variables = {}
    settings = {}

    def expand(value):
        """The value with each $name or ${name} of a shell variable the script has set filled in.

        value: a line of the script."""
        return re.sub(r"\$\{(\w+)\}|\$(\w+)",
                      lambda m: variables.get(m.group(1) or m.group(2), m.group(0)), value)

    # A command continued over several lines with a backslash is one line.
    for line in re.sub(r"\\\n", " ", text).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if m := SHELL_VARIABLE.match(line):
            variables[m.group(1)] = m.group(2).strip("\"'")
            continue
        for args in xmlchange_calls(expand(line)):
            for key, value in xmlchange_pairs(args):
                if key in RUN_SETTINGS and "$" not in value:
                    settings[RUN_SETTINGS[key]] = value
    return settings


def xmlchange_pairs(args):
    """The (KEY, value) pairs one xmlchange call sets.

    args: the call's arguments, as xmlchange_calls() gives them, such as
      ["RUN_TYPE=hybrid,RUN_REFDATE=0101-01-01"] or ["--id", "RUN_TYPE", "--val", "hybrid"]."""
    if "--id" in args:
        i = args.index("--id")
        val = args[args.index("--val") + 1] if "--val" in args[:-1] else None
        return [(args[i + 1], val)] if i + 1 < len(args) and val is not None else []
    return [tuple(p.split("=", 1)) for arg in args if not arg.startswith("-")
            for p in arg.split(",") if "=" in p]


# Stands for a part of a case name the script only knows when it runs, such as $(date).
UNKNOWN = "\0"

# Variables that may hold the case name, in a script without a create_newcase line.
CASE_VARIABLES = ("casename", "CASENAME", "case_name", "CASE_NAME", "case", "CASE")


def script_case_name(text):
    """The case name a run script creates: the last part of create_newcase's --case path, with
    the shell variables it uses filled in. A part known only when the script runs, such as
    $(date +%Y%m%d), is UNKNOWN. None if the script names no case.

    text: the run script's text."""
    # Commands continued over several lines with a backslash become one line, and $(...) is
    # replaced first, since it may hold quotes of its own.
    text = re.sub(r"\\\n", " ", text)
    text = re.sub(r"\$\((?:[^()]|\([^()]*\))*\)|`[^`]*`", UNKNOWN, text)
    variables = {}
    newcase = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if m := re.match(r"""^(?:export\s+)?([A-Za-z_]\w*)=("[^"]*"|'[^']*'|[^\s;#]*)""", line):
            variables[m.group(1)] = m.group(2).strip("\"'")
        elif m := re.search(r"create_newcase\b.*?--case(?:\s+|=)(\"[^\"]*\"|'[^']*'|[^\s;]+)", line):
            newcase = m.group(1).strip("\"'")

    def expand(value, depth=0):
        """The value with each shell variable filled in, and UNKNOWN for one the script doesn't set.

        value: the text to expand, such as the --case path.
        depth: how many variables deep this is; past ten, the value is UNKNOWN, so that variables
          that refer to each other can't loop."""
        if depth > 10:
            return UNKNOWN
        return re.sub(r"\$\{(\w+)[^}]*\}|\$(\w+)",
                      lambda m: expand(variables[m.group(1) or m.group(2)], depth + 1)
                      if (m.group(1) or m.group(2)) in variables else UNKNOWN, value)

    raw = newcase or next((variables[v] for v in CASE_VARIABLES if v in variables), None)
    if not raw:
        return None
    name = expand(raw).rstrip("/").split("/")[-1]
    return name if name.strip(UNKNOWN) else None


def name_pattern(name, unknown):
    """A regular expression for the case names that fit name, in which each unknown part
    stands for one part of a case name without a dot, such as the 20261016 of
    $(date +%Y%m%d). So n1850GHGGaxg.LM.nor30b27.002.<date> fits
    n1850GHGGaxg.LM.nor30b27.002.20261016, but not ...002.20261022.oxidtest, a test made
    with the same script, nor ...002.20261016.cmor, cmip7-prep's older output.

    name: a case name with unknown parts, such as script_case_name() gives.
    unknown: the character that marks each unknown part: UNKNOWN in a name from a run script,
      and "*" in a case name the page has, where nothing on NIRD fits it yet."""
    return re.compile("[^./]+".join(re.escape(p) for p in name.split(unknown)) + r"\Z")


def find_case(name, case_dir):
    """The case's name on NIRD and the case directories that fit it, as (name, fits). If all
    of name is known, it is (name, []). Otherwise name is the one directory, below case_dir with
    {case} as its last part, whose name fits name with any UNKNOWN part, e.g. its $(date). Of
    several that fit, it is the latest by name, and None if none fits.

    name: the case name its run script gives, from script_case_name().
    case_dir: where a case's output is on NIRD, with {case} for its name: CASE_DIR."""
    if UNKNOWN not in name:
        return name, []
    parent, last = os.path.split(case_dir)
    if last != "{case}" or not os.path.isdir(parent):
        return None, []
    pattern = name_pattern(name, UNKNOWN)
    fits = sorted(d for d in os.listdir(parent) if pattern.match(d))
    return (fits[-1] if fits else None), fits


def model_dates(directory, pattern):
    """The latest (year, month, day) among the files, and the years between the first and
    the last file that have no file. None if there are no files, False if the directory doesn't
    exist. A monthly file (no day) is dated at the start of the next month.

    directory: the directory of the files, such as a case's atm/hist.
    pattern: a shell-style pattern for the files' names, such as HIST_PATTERN. A file is dated
      by the date DATE finds just before .nc."""
    if not os.path.isdir(directory):
        return False
    dates = []
    years = set()
    for name in os.listdir(directory):
        if not fnmatch.fnmatchcase(name, pattern):
            continue
        m = DATE.search(name)
        if not m:
            continue
        y = int(m.group(1))
        mo = int(m.group(2))
        d = m.group(3)
        years.add(y)
        if d is None:
            # A monthly file is dated at the start of the next month.
            if mo == 12:
                y += 1
                mo = 1
            else:
                mo += 1
            d = 1
        dates.append((y, mo, int(d)))
    if not dates:
        return None
    return max(dates), [y for y in range(min(years), max(years)) if y not in years]


def step_logs(logs):
    """{step: modification time} for the step logs in a directory of cmip7-prep's logs. run.log,
    the record of every run, is not a step's log.

    logs: logs/<first>-<last>/, or logs/ itself for a run of every year."""
    times = {}
    for n in os.listdir(logs) if os.path.isdir(logs) else []:
        if n.endswith(".log") and n != "run.log" and os.path.isfile(p := os.path.join(logs, n)):
            try:
                times[n[:-len(".log")]] = os.path.getmtime(p)
            except OSError:
                pass
    return times


def output_years(directory, case):
    """(first, last): the model years cmip7-prep's output for the case covers so far, from the
    time ranges in the names of its CMOR files and time series; None if it has none yet.

    directory: cmip7-prep's output directory for the case, cmip7prep/<case>.
    case: the case's name, which is taken out of a time series' name before it is dated."""
    years = set()
    for m, _, _ in cmor_files(os.path.join(directory, "cmor")):
        years.update((int(m.group(2)), int(m.group(3))))
    for d, _, names in os.walk(os.path.join(directory, "timeseries")):
        for name in names:
            if name.endswith(".nc") and (ys := file_years(name.replace(case, ""))):
                years.update((ys[0], ys[-1]))
    return (min(years), max(years)) if years else None


def prep_changed(directory, since):
    """Whether cmip7-prep has worked on the case since the time: whether a record of finished
    steps, run.log or a step log has changed since. It reads only those small files, so it
    is cheap even when the output is large.

    directory: cmip7-prep's output directory for the case, cmip7prep/<case>.
    since: the time, as seconds since the epoch, such as the last progress.json's; None for
      always."""
    if since is None:
        return True
    logs = os.path.join(directory, "logs")
    paths = glob.glob(os.path.join(glob.escape(directory), "completed_steps*.json"))
    paths += glob.glob(os.path.join(glob.escape(logs), "*.log"))
    paths += glob.glob(os.path.join(glob.escape(logs), "*", "*.log"))
    for path in paths:
        try:
            if os.path.getmtime(path) > since:
                return True
        except OSError:
            pass
    return False


def cmor_sets(directory, case):
    """[{first, last, steps, failed, lastLog}] for each set of years cmip7-prep has started.
    A run with --years makes logs/<first>-<last>/ and completed_steps_<first>-<last>.json. A
    run of every year puts its step logs in logs/ and writes completed_steps.json; its years
    are those its output covers so far, and until it has output it is left out. steps are the
    cmor- steps the json lists, the ones that finished; a set with no json yet has none.
    failed are the cmor- steps that have a log but are not listed: they failed or are still
    running. The json alone can't show a realm that failed in every set, since it lists no
    failures.

    lastLog, for a set with step logs, is the step whose log was written last, as
    {step, time, done, stale}. cmip7-prep writes to a step's log while the step runs. A step
    that isn't done may be running or may have failed; once its log hasn't changed for
    STALE_LOG, it may have stopped. done is whether the json lists the step.

    directory: cmip7-prep's output directory for the case, cmip7prep/<case>.
    case: the case's name, as output_years() takes it."""
    logs_dir = os.path.join(directory, "logs")
    # Each set's (first, last), its json and its directory of step logs.
    found_years = set()
    for path in glob.glob(os.path.join(glob.escape(directory), "completed_steps_*.json")):
        if m := COMPLETED.search(os.path.basename(path)):
            found_years.add((int(m.group(1)), int(m.group(2))))
    for name in os.listdir(logs_dir) if os.path.isdir(logs_dir) else []:
        if (m := LOG_YEARS.fullmatch(name)) and os.path.isdir(os.path.join(logs_dir, name)):
            found_years.add((int(m.group(1)), int(m.group(2))))
    found = [(first, last, os.path.join(directory, f"completed_steps_{first}-{last}.json"),
              os.path.join(logs_dir, f"{first}-{last}")) for first, last in sorted(found_years)]
    every_year = os.path.join(directory, "completed_steps.json")
    if os.path.isfile(every_year) or step_logs(logs_dir):
        if years := output_years(directory, case):
            found.append((*years, every_year, logs_dir))
        else:
            print(f"note: {directory} has a run of every year with no output yet, so it is not shown")
    now = datetime.datetime.now().astimezone()
    sets = []
    for first, last, path, logs in found:
        # cmip7-prep rewrites the json after every step, so it may be read half-written. Then
        # the set counts as having no step finished on this run, and is still shown.
        listed = set()
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    found = json.load(f)
                listed = {k for k in found if isinstance(k, str)} if isinstance(found, list) else set()
            except (OSError, ValueError):
                print(f"warning: {path} could not be read; its steps count as not finished")
        steps = sorted(k for k in listed if k.startswith("cmor-"))
        times = step_logs(logs)
        ran = {k for k in times if k.startswith("cmor-")}
        s = {"first": first, "last": last, "steps": steps, "failed": sorted(ran - set(steps))}
        if times:
            step = max(times, key=times.get)
            when = datetime.datetime.fromtimestamp(times[step]).astimezone()
            s["lastLog"] = {"step": step, "time": when.strftime("%Y-%m-%d %H:%M %Z"),
                            "done": step in listed, "stale": now - when > STALE_LOG}
        sets.append(s)
    return sets


# What the tracker writes next to progress.json gets the group of that directory, and is
# writable by the group and readable by everyone, as the web server needs. So anyone in the
# group can run the script after someone else has. A directory is setgid, so that what is
# made in it gets its group too.
SHARED_FILE = 0o664
SHARED_DIR = 0o2775


def share(path, gid, mode):
    """Gives a file or directory this run has made the group and the mode, so that another
    member of the group can replace it on a later run.

    path: the file or directory.
    gid: the group of the directory progress.json is in.
    mode: SHARED_FILE for a file, SHARED_DIR for a directory."""
    # The group is set first, since changing it can clear the setgid bit.
    os.chown(path, -1, gid)
    os.chmod(path, mode)


def shared_temp(path, gid):
    """The path of a new, empty file next to path, named after it and this run, such as
    .progress.json.k3j9x2.tmp, shared as share() does. Each run's name is its own, so a file
    left by a run that was stopped can't get in the way of the next.

    path: the file the temporary file is to replace once it is written.
    gid: the group of the directory progress.json is in."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)),
                               prefix=f".{os.path.basename(path)}.", suffix=".tmp")
    os.close(fd)
    try:
        share(tmp, gid, SHARED_FILE)
    except OSError:
        os.remove(tmp)
        raise
    return tmp


def make_shared_dir(path, gid):
    """Makes the directory, and any of its parents that don't exist, shared as share() does.

    path: the directory, such as validation/<case> next to progress.json.
    gid: the group of the directory progress.json is in."""
    if os.path.isdir(path):
        return
    make_shared_dir(os.path.dirname(path), gid)
    os.mkdir(path)
    share(path, gid, SHARED_DIR)


def sync_report(source, dest, gid):
    """Makes dest a copy of the directory source: copies each file that dest lacks or has at
    another size or modification time, and removes from dest what source no longer has.
    What it makes is shared as share() does, and each file is copied to a temporary file
    and then moved into place, so that it can replace a file another user copied.

    source: a case's validation report directory, <cmip7-prep output>/cmor/validation_reports.
    dest: where its copy goes, validation/<case> next to progress.json. It is created if it
      doesn't exist.
    gid: the group of the directory progress.json is in."""
    for root, dirs, files in os.walk(source):
        target = os.path.normpath(os.path.join(dest, os.path.relpath(root, source)))
        make_shared_dir(target, gid)
        for name in files:
            src = os.path.join(root, name)
            dst = os.path.join(target, name)
            s = os.stat(src)
            if not os.path.isfile(dst) or os.path.getsize(dst) != s.st_size or int(os.path.getmtime(dst)) != int(s.st_mtime):
                tmp = shared_temp(dst, gid)
                try:
                    shutil.copyfile(src, tmp)
                    # The copy keeps the original's time, by which the next run tells
                    # whether it has changed.
                    os.utime(tmp, (s.st_atime, s.st_mtime))
                    os.replace(tmp, dst)
                except OSError:
                    os.remove(tmp)
                    raise
    for root, dirs, files in os.walk(dest, topdown=False):
        original = os.path.join(source, os.path.relpath(root, dest))
        for name in files:
            if not os.path.isfile(os.path.join(original, name)):
                os.remove(os.path.join(root, name))
        if not os.path.isdir(original):
            os.rmdir(root)


_cmor_files = {}


def file_version(dirpath):
    """The version directory, vYYYYMMDD, nearest above a file; None if there is none.

    dirpath: the directory the file is in."""
    return next((p for p in reversed(dirpath.split(os.sep)) if VERSION_DIR.match(p)), None)


def cmor_files(directory):
    """A list of (name match, version, bytes), one for each CMOR file with a time range under
    the directory, read once per run. The name match is CMOR_FILE's: group 1 is the name before
    the time range, from the variable to the grid, and groups 2 and 3 are its first and last
    year. The version is file_version()'s.

    directory: one tool's CMOR output directory, such as cmip7prep/<case>/cmor or CMOR4CMIP7_DIR."""
    if directory not in _cmor_files:
        _cmor_files[directory] = [(m, file_version(d), size(os.path.join(d, name)))
                                  for d, _, names in os.walk(directory)
                                  for name in names if (m := CMOR_FILE.match(name))]
    return _cmor_files[directory]


def size(path):
    """The file's size in bytes, 0 if it can't be read.

    path: the file's path."""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def file_years(name):
    """The model years a file's name covers: from its first to its last date, written as
    YYYY, YYYY-MM[-DD[-SSSSS]] or YYYYMM[DD].

    name: the file's name, with the case name taken out, since its date is not a model date."""
    years = [int(m.group(1)) for m in NAME_DATE.finditer(name)]
    return range(min(years), max(years) + 1) if years else range(0)


def output_size(paths, case):
    """{gib, years} for the netCDF files: their size in GiB, and how many model years their
    names cover; None if there are no dated ones.

    paths: the paths of the files, such as files_below() gives for a hist directory.
    case: the case's name, which is taken out of each file's name before it is dated."""
    years = set()
    total = 0
    for path in paths:
        if path.endswith(".nc") and (ys := file_years(os.path.basename(path).replace(case, ""))):
            years.update(ys)
            total += size(path)
    return {"gib": round(total / BYTES_PER_GIB, 3), "years": len(years)} if years else None


def files_below(directory, skip=None):
    """The paths of the files below the directory, leaving out its subdirectory skip.

    directory: the directory to search.
    skip: the name of a subdirectory of directory to leave out, such as "cmor"; None to leave
      out nothing."""
    paths = []
    for d, subdirs, names in os.walk(directory):
        if d == directory and skip in subdirs:
            subdirs.remove(skip)
        paths += [os.path.join(d, n) for n in names]
    return paths


def settled_storage(storage):
    """The kinds of a case's earlier measurement to keep: those with at least
    STORAGE_YEARS years, by then a steady rate, and in GiB. History counts its longest
    component, and CMOR is kept tool by tool. Restarts are measured on every run.

    storage: the case's "storage" in the last progress.json, as case_storage() gave it. Anything
      else, such as None when the case has none, keeps nothing."""
    storage = storage if isinstance(storage, dict) else {}

    def settled(v):
        """Whether one kind's measurement is in GiB and covers at least STORAGE_YEARS years.

        v: the measurement, {gib, years}."""
        return isinstance(v, dict) and "gib" in v and v.get("years", 0) >= STORAGE_YEARS

    kept = {}
    if settled(storage.get("timeseries")):
        kept["timeseries"] = storage["timeseries"]
    hist = storage.get("hist") if isinstance(storage.get("hist"), dict) else {}
    if hist and all("gib" in v for v in hist.values()) and any(settled(v) for v in hist.values()):
        kept["hist"] = hist
    cmor = storage.get("cmor") if isinstance(storage.get("cmor"), dict) else {}
    if cmor := {t: v for t, v in cmor.items() if settled(v)}:
        kept["cmor"] = cmor
    return kept


def restart_sets(rest):
    """The restart sets kept in rest/, as {gib, sets, setGib, everyYears}: their total size,
    how many there are, the mean size of one, and the years between one set and the next,
    from the dates the sets' directories are named after (None with fewer than two sets).
    rest/ with files but no dated directories is one set. None if rest/ is empty.

    rest: the case's rest/ directory."""
    if not os.path.isdir(rest):
        return None
    dated = sorted(d for d in os.listdir(rest) if REST_SET.match(d) and os.path.isdir(os.path.join(rest, d)))
    paths = files_below(rest)
    if not paths:
        return None
    total = sum(map(size, paths)) / BYTES_PER_GIB
    sets = max(len(dated), 1)
    every = None
    if len(dated) > 1:
        # A date as a fractional year: 0101-07-01 is 101.5.
        when = [int(m.group(1)) + (int(m.group(2)) - 1) / 12 + (int(m.group(3)) - 1) / 365
                for m in map(REST_SET.match, dated)]
        gaps = sorted(b - a for a, b in zip(when, when[1:]))
        every = round(gaps[len(gaps) // 2], 2)
    return {"gib": round(total, 3), "sets": sets, "setGib": round(total / sets, 3), "everyYears": every}


def case_storage(case_dir, case, prep_dir, cmor4cmip7_dir, experiment, variant, kept=None):
    """How much output the case has on NIRD, in GiB: as {gib, years} for hist, a dict by
    component of the <component>/hist directories; timeseries, cmip7-prep's time series, the
    files in its directory outside cmor/; and cmor, by tool, the CMOR files, each version's
    years counted. rest is the restart sets in rest/, as restart_sets() gives them. A kind with
    nothing on NIRD is left out.

    case_dir: the directory of the case's output on NIRD, CASE_DIR with its name.
    case: the case's name.
    prep_dir: cmip7-prep's output directory for the case; None if it has none.
    cmor4cmip7_dir: cmor4cmip7's output directory for the case; None if it has none.
    experiment: the case's experiment, such as piControl, which with the variant picks the
      case's CMOR files, as in case_files().
    variant: the case's variant, such as r1i1p1f1.
    kept: the kinds of the last run's measurement to keep instead of measuring them again, as
      settled_storage() gives them; None to measure them all."""
    kept = kept or {}
    storage = {k: v for k, v in kept.items() if k != "cmor"}
    if case_dir and os.path.isdir(case_dir):
        if "hist" not in storage:
            hist = {}
            for comp in sorted(os.listdir(case_dir)):
                if os.path.isdir(d := os.path.join(case_dir, comp, "hist")) and (s := output_size(files_below(d), case)):
                    hist[comp] = s
            if hist:
                storage["hist"] = hist
        if rest := restart_sets(os.path.join(case_dir, "rest")):
            storage["rest"] = rest
    if "timeseries" not in storage and prep_dir and (s := output_size(files_below(prep_dir, skip="cmor"), case)):
        storage["timeseries"] = s
    cmor = dict(kept.get("cmor", {}))
    prep_cmor = os.path.join(prep_dir, "cmor") if prep_dir else None
    for tool, d in (("cmip7prep", prep_cmor), ("cmor4cmip7", cmor4cmip7_dir)):
        if tool not in cmor and d and os.path.isdir(d):
            by_version = {}
            total = 0
            for m, v, b in case_files(d, experiment, variant):
                by_version.setdefault(v, set()).update(range(int(m.group(2)), int(m.group(3)) + 1))
                total += b
            if years := sum(len(ys) for ys in by_version.values()):
                cmor[tool] = {"gib": round(total / BYTES_PER_GIB, 3), "years": years}
    if cmor:
        storage["cmor"] = cmor
    return storage


def year_ranges(years):
    """[[first, last], ...] for the runs of consecutive years.

    years: the years, as integers, in any order."""
    ranges = []
    for y in sorted(years):
        if ranges and y == ranges[-1][1] + 1:
            ranges[-1][1] = y
        else:
            ranges.append([y, y])
    return ranges


def case_files(directory, experiment, variant):
    """A list of (name match, version, bytes), as cmor_files() gives them, one for each of the
    case's CMOR files under the directory: those whose name has its experiment followed by
    its variant, as in so_Omon_NorESM3_piControl_r1i1p1f1_gn_000101-001012.nc.

    directory: one tool's CMOR output directory, such as cmip7prep/<case>/cmor or CMOR4CMIP7_DIR.
    experiment: the case's experiment, from its run script's directory, such as piControl.
    variant: the case's variant, from its issue's label, such as r1i1p1f1.
    Without both, the case has no files."""
    if not experiment or not variant:
        return []
    key = f"_{experiment}_{variant}_"
    return [(m, v, b) for m, v, b in cmor_files(directory) if key in m.group(0)]


def cmor_file_years(directory, experiment, variant):
    """[[first, last], ...]: the years every variable of the case has a file for under the
    directory, from the time ranges in the CMOR file names. Files without a time range
    (fixed fields) don't count.

    directory: one tool's CMOR output directory, such as CMOR4CMIP7_DIR.
    experiment, variant: the case's, which pick its files, as in case_files()."""
    by_var = {}
    for m, _, _ in case_files(directory, experiment, variant):
        by_var.setdefault(m.group(1), set()).update(range(int(m.group(2)), int(m.group(3)) + 1))
    return year_ranges(set.intersection(*by_var.values())) if by_var else []


def cmor_versions(directory, experiment, variant):
    """{version: [[first, last], ...]}: the years of the case's CMOR files under the
    directory, by the version directory they are in.

    directory: one tool's CMOR output directory, such as cmip7prep/<case>/cmor or CMOR4CMIP7_DIR.
    experiment, variant: the case's, which pick its files, as in case_files()."""
    by_version = {}
    for m, v, _ in case_files(directory, experiment, variant):
        if v:
            by_version.setdefault(v, set()).update(range(int(m.group(2)), int(m.group(3)) + 1))
    return {v: year_ranges(ys) for v, ys in sorted(by_version.items())}


def read_timing_file(path):
    """{cost, sypd, days, pes, machine, date} from one timing file; None if it lacks cost or
    throughput. The date is when the job ended (Curr Date), or the file's time without one.

    path: the timing file, noresm_timing.<case>.<job id>.<date>, gzipped or not."""
    opener = gzip.open if path.endswith(".gz") else open
    try:
        with opener(path, "rt", encoding="utf-8", errors="replace") as f:
            text = f.read()
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    found = {k: m.group(1) for k, r in TIMING_LINES.items() if (m := r.search(text))}
    if "cost" not in found or "sypd" not in found or float(found["sypd"]) <= 0:
        return None
    t = {k: float(found[k]) for k in ("cost", "sypd", "days") if k in found}
    if "pes" in found:
        t["pes"] = int(found["pes"])
    if "machine" in found:
        t["machine"] = found["machine"]
    try:
        t["date"] = datetime.datetime.strptime(found.get("date", ""), "%a %b %d %H:%M:%S %Y")
    except ValueError:
        t["date"] = datetime.datetime.fromtimestamp(mtime)
    return t


def case_timing(directory):
    """The case's cost and throughput on each machine it ran on, averaged over all its
    timing files there with each job weighted by the years it simulated: cost in pe-hours,
    which are core-hours, per simulated year, and throughput in simulated years per day. Each
    machine's entry also lists its jobs, [end date, cost, throughput, years], for the page's
    plot of how the timings vary. Returns ([{machine, jobs, years, cost, sypd, pes, runs}], the
    machine of its latest job), or (None, None) if there are no readable timing files.

    directory: the case's timing directory, <case>/<case>/timing in its archive."""
    paths = glob.glob(os.path.join(glob.escape(directory), "noresm_timing.*"))
    jobs = sorted((t for path in paths if (t := read_timing_file(path))), key=lambda t: t["date"])
    if not jobs:
        return None, None
    by_machine = {}
    for t in jobs:
        by_machine.setdefault(t.get("machine", "unknown").lower(), []).append(t)
    timing = []
    for machine, mjobs in sorted(by_machine.items()):
        years = [t.get("days", 365) / 365 for t in mjobs]
        total = sum(years)
        entry = {"machine": machine, "jobs": len(mjobs), "years": round(total, 2),
                 "cost": round(sum(t["cost"] * y for t, y in zip(mjobs, years)) / total, 2),
                 "sypd": round(total / sum(y / t["sypd"] for t, y in zip(mjobs, years)), 2)}
        if "pes" in mjobs[-1]:
            entry["pes"] = mjobs[-1]["pes"]
        entry["runs"] = [[t["date"].strftime("%Y-%m-%d %H:%M"), t["cost"], t["sypd"], round(y, 2)]
                         for t, y in zip(mjobs, years)]
        timing.append(entry)
    return timing, jobs[-1].get("machine", "unknown").lower()


def main():
    """Reads the case issues and the CMORization issues on GitHub, the run scripts on the
    CMIP7_Runscripts branch, the case directories on NIRD and the spreadsheet, and writes
    progress.json and the page. Raises Failed, and leaves both files as they were, if anything
    it needs can't be read, the page can't be built, or the files can't be written. Only a
    failure to write them comes after the validation reports have been copied.

    On NIRD the script is run with no arguments. Its two arguments are for testing a change
    without touching the published page, and for measuring the storage again."""
    parser = argparse.ArgumentParser()
    parser.add_argument("out", nargs="?", default=PROGRESS,
                        help=f"where to write progress.json; the page, index.html, goes next to it (default {PROGRESS})")
    parser.add_argument("--remeasure-storage", action="store_true",
                        help="measure every case's storage on NIRD again, instead of keeping what the "
                             "last progress.json has for it; the cost and throughput are read again "
                             "on every run regardless")
    args = parser.parse_args()

    # Each case's output as the last run measured it, kept where it covers enough years, and
    # when that run was.
    earlier = {}
    earlier_time = None
    if not args.remeasure_storage:
        try:
            with open(args.out, encoding="utf-8") as f:
                # By issue and case, so a case whose name has changed is measured again.
                earlier = {(c["issue"], c.get("case")): settled_storage(c.get("storage"))
                           for c in json.load(f)["cases"]}
            earlier_time = os.path.getmtime(args.out)
        except (OSError, ValueError, KeyError, TypeError):
            earlier = {}

    try:
        issues = fetch_issues(REPO)
    except (OSError, ValueError, http.client.HTTPException) as e:
        raise Failed(f"The case issues in {REPO} could not be read: {e}") from e
    # The repository always has case issues, so none means GitHub's reply was wrong, and a
    # page without them would show no progress at all.
    if not issues:
        raise Failed(f"No issues were read from {REPO}.")
    # Only an issue whose title is a name, of letters, digits, ".", "-" and "_", is a case.
    # Any other title, such as one with a space, is a note or a placeholder for a run not
    # set up yet, and the issue is left out.
    named = []
    for issue in issues:
        if TITLE_CHARACTERS.fullmatch(issue["title"].strip()):
            named.append(issue)
        else:
            print(f"#{issue['number']}: skipped, its title is not a name: {issue['title'].strip()!r}")
    issues = named

    # A case's CMORization issues are those in CMIP7_CMOR-Publish that name the case.
    try:
        cmor_issues = fetch_issues(CMOR_REPO)
    except (OSError, ValueError, http.client.HTTPException) as e:
        raise Failed(f"The CMORization issues in {CMOR_REPO} could not be read: {e}") from e
    # A case's run script is the one whose name starts with its issue number.
    scripts, why = list_run_scripts(REPO)
    if scripts is None:
        raise Failed(f"The run scripts could not be listed: {why}")
    if not scripts:
        raise Failed(f"No run scripts were found in scripts/ on the {RUNSCRIPTS_BRANCH} branch of {REPO}.")
    scripts_of_issue = {}
    for path in sorted(scripts):
        if m := SCRIPT_ISSUE.match(os.path.basename(path)):
            scripts_of_issue.setdefault(int(m.group(1)), []).append(path)

    # Each issue's case is set up by its run script. The case name is the one the script gives
    # create_newcase, so the issue's title doesn't have to be the case name. The part of the
    # name the script only knows when it runs, such as $(date), comes from the case's
    # directory on NIRD. A script that names no case falls back to the issue's title. The
    # case's experiment is the directory its run script is in, scripts/<MIP>/<experiment>/.
    # An issue with no run script yet is left out, since its experiment is not known.
    tracked = []
    skipped = []
    for issue in sorted(issues, key=lambda i: i["number"]):
        num = issue["number"]
        title = issue["title"].strip()
        labels = [l["name"] for l in issue.get("labels", [])]
        if "test" in labels:
            continue
        script_paths = scripts_of_issue.get(num, [])
        if not script_paths:
            reason = (f"no run script yet: no script in scripts/ on the {RUNSCRIPTS_BRANCH} branch "
                      f"has a name starting with its issue number, {num:03d}")
            skipped.append({"issue": num, "title": title, "reason": reason})
            print(f"#{num} {title}: skipped, {reason}")
            continue
        if len(script_paths) > 1:
            raise Failed(f"#{num} has more than one run script: " + ", ".join(script_paths)
                         + ". Only one script's name may start with its issue number.")
        script = script_paths[0]
        parts = script.split("/")
        if len(parts) != 3:
            raise Failed(f"#{num}'s run script, scripts/{script}, is not in an experiment directory: "
                         "run scripts go in scripts/<MIP>/<experiment>/.")
        experiment = parts[1]
        text, why = read_run_script(REPO, script)
        if text is None:
            raise Failed(f"#{num}'s run script, scripts/{script}, could not be read: {why}")
        name = None
        notes = []
        if pattern := script_case_name(text):
            name, fits = find_case(pattern, CASE_DIR)
            if len(fits) > 1:
                notes.append("more than one case directory fits its run script: " + ", ".join(fits)
                             + f"; {name} is used")
            if not name:
                # Nothing on NIRD yet: the name as far as the script knows it.
                name = pattern.replace(UNKNOWN, "*")
        else:
            notes.append("its run script names no case; the title is used")
        tracked.append((issue, num, title, labels, script, text, name or title, experiment, notes))
    # A case's parent is the issue whose case is its RUN_REFCASE.
    issue_of_case = {name: num for _, num, _, _, _, _, name, _, _ in tracked}

    cases = []
    # Each case's validation report, by case name, to be copied next to progress.json.
    reports = {}
    for issue, num, title, labels, script, text, name, experiment, notes in tracked:
        m = TITLE.match(name)
        if not m:
            skipped.append({"issue": num, "title": title, "reason": f"{name} is not a case name"})
            print(f"#{num} {title}: skipped, {name} is not a case name")
            continue
        if name != title and "*" not in name:
            notes.append(f"its case is {name}")
        people = [u["login"] for u in issue.get("assignees") or []] or [issue["user"]["login"]]
        # The issue's labels give only the variant, such as r1i1p1f1, and whether the case is
        # completed or on ESGF.
        variant = next((l for l in labels if VARIANT.match(l)), None)
        case = {"issue": num, "case": name, "resolution": RESOLUTION[m.group(1)],
                "version": m.group(2), "experiment": experiment, "owner": ", ".join(people),
                "labels": labels, "completed": "completed" in labels}
        if variant:
            case["variant"] = variant
        found_dates = model_dates(HIST_DIR.format(case=name), HIST_PATTERN)
        if found_dates:
            last, missing = found_dates
            case["modelDate"] = "%04d-%02d-%02d" % last
            if missing:
                # The years with no file, as [[first, last], ...], e.g. [[1854, 1855], [1857, 1857]].
                case["missingYears"] = year_ranges(missing)
        # Each tool's output directory, where it exists. cmip7-prep's is the case's
        # directory in cmip7prep/, on datapeak or else on datalake. cmor4cmip7's is the
        # simulation's, from the case's resolution, experiment and variant.
        output = {}
        if d := next((d for d in (p.format(case=name) for p in CMIP7PREP_DIRS) if os.path.isdir(d)), None):
            output["cmip7prep"] = d
        if variant:
            d = CMOR4CMIP7_DIR.format(resolution=case["resolution"], experiment=experiment, variant=variant)
            if os.path.isdir(d):
                output["cmor4cmip7"] = d
        if output:
            case["cmorOutput"] = output
        case["runScript"] = script
        if start := run_settings(text):
            # The parent's case name may still have a * for a part only NIRD knows.
            refcase = start.get("refcase", "")
            if parent := issue_of_case.get(refcase) or next(
                    (n for c, n in issue_of_case.items() if "*" in c and name_pattern(c, "*").match(refcase)), None):
                start["parent"] = parent
            case["start"] = start
        # The name must stand on its own, so that a longer name that starts with it, such as
        # a test case's ...002.20261016.oxid, does not count. A full stop after it may end a
        # sentence.
        named = re.compile(r"(?<![\w.-])" + re.escape(name) + r"(?![\w-])(?!\.[\w-])")
        if found := [i for i in cmor_issues if named.search(i["title"] + "\n" + (i.get("body") or ""))]:
            case["cmorIssues"] = []
            for i in found:
                ci = {"number": i["number"], "state": i["state"]}
                if t := CMOR_TAG_VERSION.search(i.get("body") or ""):
                    ci["tagVersion"] = "v" + t.group(1)
                case["cmorIssues"].append(ci)
        versions = {}
        if prep_dir := output.get("cmip7prep"):
            sets = cmor_sets(prep_dir, name)
            if sets:
                case["cmor"] = sets
            # cmip7-prep writes its CMOR files under cmor/ in its output directory.
            prep_cmor = os.path.join(prep_dir, "cmor")
            if v := cmor_versions(prep_cmor if os.path.isdir(prep_cmor) else prep_dir, experiment, variant):
                versions["cmip7prep"] = v
            # The validation report is copied next to progress.json once the page is built.
            report = os.path.join(prep_dir, VALIDATION_REPORT)
            if os.path.isfile(os.path.join(report, "index.html")) and TITLE_CHARACTERS.fullmatch(name):
                reports[name] = report
                case["validationReport"] = f"{VALIDATION_COPIES}/{name}/index.html"
        if "cmor4cmip7" in output:
            ranges = cmor_file_years(output["cmor4cmip7"], experiment, variant)
            if ranges:
                case["cmor4cmip7"] = ranges
            if v := cmor_versions(output["cmor4cmip7"], experiment, variant):
                versions["cmor4cmip7"] = v
        if versions:
            case["cmorVersions"] = versions
        kept = dict(earlier.get((num, name), {}))
        # cmip7-prep's output grows a realm at a time, so a measurement made partway through a
        # set is not a steady rate: it is measured again whenever cmip7-prep has worked on the
        # case since the last run.
        if prep_dir and prep_changed(prep_dir, earlier_time):
            kept.pop("timeseries", None)
            if "cmor" in kept:
                kept["cmor"] = {t: v for t, v in kept["cmor"].items() if t != "cmip7prep"}
        storage = case_storage(CASE_DIR.format(case=name), name,
                               prep_dir, output.get("cmor4cmip7"), experiment, variant, kept)
        if storage:
            case["storage"] = storage
        # Each case's timing files are in <case>/<case>/timing in its archive.
        timing_dir = (CASE_DIR + "/{case}/timing").format(case=name)
        if os.path.isdir(timing_dir):
            timing, machine = case_timing(timing_dir)
            if timing:
                case["timing"], case["machine"] = timing, machine
        cases.append(case)
        # The line printed for each case gives its latest model date and its start date.
        if "modelDate" in case:
            report = f"at {case['modelDate']}"
            if startdate := case.get("start", {}).get("startdate"):
                report += f"; start date {startdate}"
        else:
            report = "no output on NIRD" if found_dates is False else "no dated files"
        # Problems with the run script are printed with the case's line.
        print(f"#{num} {title} [{', '.join(labels) or 'no labels'}]: " + "; ".join([report] + notes))

    # When the progress was written, in NIRD's local time with its time zone, e.g. CET.
    now = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    data = {"updated": now, "repo": REPO, "cases": cases, "skipped": skipped}
    # The page, index.html next to progress.json, built from the spreadsheet, the plan and the
    # template; it reads progress.json when it is opened. It is built before anything is
    # written, so that if it can't be built neither file is updated.
    page = os.path.join(os.path.dirname(os.path.abspath(args.out)), "index.html")
    sheet = {"url": f"{SHEET}/edit?gid={SHEET_TAB}#gid={SHEET_TAB}", "read": now}
    sheet_text = fetch_sheet()
    try:
        text, _, sims, warnings = build_scheduler.page_text(sheet_text, sheet)
    except (OSError, ValueError) as e:
        raise Failed(f"The page could not be built: {e}") from e
    if not sims:
        raise Failed("The page could not be built: the spreadsheet has no runs in the plan.")

    # Each case's validation report is copied next to progress.json, where the web server
    # serves it, and only what has changed since the last run is copied. A report that can't
    # be copied is left without a link, with a warning, rather than stopping the update.
    # What is written next to progress.json gets the group of its directory, as share() says.
    directory = os.path.dirname(os.path.abspath(args.out))
    try:
        gid = os.stat(directory).st_gid
    except OSError as e:
        raise Failed(f"{directory} can't be written to: {e}") from e
    copies = os.path.join(directory, VALIDATION_COPIES)
    for case in cases:
        if case.get("case") not in reports:
            continue
        try:
            sync_report(reports[case["case"]], os.path.join(copies, case["case"]), gid)
        except OSError as e:
            del case["validationReport"]
            print(f"warning: #{case['issue']}'s validation report could not be copied: {e}")
    # Both files are written to temporary files first and then moved into place, so the page
    # never reads a half-written file.
    temps = {}
    try:
        for path, content in ((args.out, json.dumps(data, indent=1)), (page, text)):
            temps[path] = shared_temp(path, gid)
            with open(temps[path], "w", encoding="utf-8") as f:
                f.write(content)
    except OSError as e:
        for tmp in temps.values():
            if os.path.exists(tmp):
                os.remove(tmp)
        raise Failed(f"{path} could not be written: {e}", copied=bool(reports)) from e
    for path, tmp in temps.items():
        os.replace(tmp, path)
    for w in warnings:
        print("warning:", w)
    print(f"{args.out}: {len(cases)} cases")
    print(f"{page}: {len(sims)} runs")


if __name__ == "__main__":
    try:
        main()
    except Failed as e:
        sys.exit(f"{e}\nprogress.json and the page are as they were."
                 + (f" The validation report copies in {VALIDATION_COPIES}/ may have been updated."
                    if e.copied else " Nothing was updated."))
    except (OSError, ValueError, http.client.HTTPException) as e:
        sys.exit(f"GitHub, the spreadsheet or NIRD could not be read: {e}\nNothing was updated: progress.json and the page are as they were.")
