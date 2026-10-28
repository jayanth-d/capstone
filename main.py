"""
main.py
=======
Builds a self-contained HTML dashboard from a completed simulation run.

Expects the project folder to look like this:

    PythonProject1/
        main.py
        5682002.xlsx          <- ABS Monthly Household Spending Indicator
        results/
            households_2016-01.csv
            households_2016-02.csv
            ...

For every month it sums each category across all households, sums those to get
the month's total, and expresses each category as a share of that total. The
same is done for the ABS benchmark, so the two are compared on shares rather
than on levels -- the simulation has a thousand households and the benchmark
has the whole country.

Just run it. It writes dashboard.html next to this file and opens it.
"""
from __future__ import annotations

import json
import webbrowser
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------- settings
# Everything you might want to change is here.

HERE = Path(__file__).resolve().parent

RESULTS_DIR = HERE / "results"          # folder of households_YYYY-MM.csv
MHSI_FILE = HERE / "5682002.xlsx"       # the ABS workbook
OUTPUT_FILE = HERE / "dashboard.html"   # where the dashboard is written

STUDENT_NAME = "Naga Jayanth Varma Dandu"
STUDENT_ID = "S4924298"

OPEN_IN_BROWSER = True                  # set False to just write the file

CATEGORIES = [
    "food", "alcohol_tobacco", "clothing_footwear", "furnishings",
    "health", "transport", "recreation", "hotels_cafes", "miscellaneous",
]

LABELS = {
    "food": "Food and groceries",
    "alcohol_tobacco": "Alcohol and tobacco",
    "clothing_footwear": "Clothing and footwear",
    "furnishings": "Furnishings and household goods",
    "health": "Health",
    "transport": "Transport",
    "recreation": "Recreation and culture",
    "hotels_cafes": "Restaurants, cafes and takeaway",
    "miscellaneous": "Other goods and services",
}

# MHSI publishes its own category names; these map onto ours.
MHSI_NAMES = {
    "Food": "food",
    "Alcoholic beverages and tobacco": "alcohol_tobacco",
    "Clothing and footwear": "clothing_footwear",
    "Furnishings and household equipment": "furnishings",
    "Health": "health",
    "Transport": "transport",
    "Recreation and culture": "recreation",
    "Hotels, cafes and restaurants": "hotels_cafes",
    "Miscellaneous goods and services": "miscellaneous",
}

# The restriction window shown on the correlation page, matching the one the
# report uses: February 2020 is the last month before restrictions and
# November 2021 the last restricted month, which gives 22 months and so 21
# monthly changes. Extending the window past the restrictions, as far as the
# end of the Omicron wave in March 2022, pulls the restaurant and alcohol
# correlations towards zero, because the reopening is a movement the
# simulation does not follow.
COVID_FIRST, COVID_LAST = "2020-02", "2021-11"


def load_simulation(results_dir: Path) -> pd.DataFrame:
    """Total dollars spent per category per month, summed across households."""
    files = sorted(results_dir.glob("households_*.csv"))
    if not files:
        # also accept the layout the cluster writes, with a decisions subfolder
        files = sorted((results_dir / "decisions").glob("households_*.csv"))
    if not files:
        raise FileNotFoundError(
            f"No households_YYYY-MM.csv files found in {results_dir}.\n"
            f"Put the monthly CSVs in a folder called 'results' next to "
            f"this script.")

    print(f"reading {len(files)} monthly files from {results_dir.name}/")
    rows = {}
    for path in files:
        month = path.stem.replace("households_", "")
        frame = pd.read_csv(path, usecols=[f"spend_{c}" for c in CATEGORIES])
        rows[month] = {c: float(frame[f"spend_{c}"].sum()) for c in CATEGORIES}
    return pd.DataFrame(rows).T.sort_index()


def load_mhsi(workbook: Path) -> pd.DataFrame:
    """Monthly national spending by category, in millions of dollars."""
    if not workbook.exists():
        raise FileNotFoundError(
            f"Could not find {workbook.name}.\n"
            f"Download ABS 5682.0 Table 2 and put it next to this script.")

    raw = pd.read_excel(workbook, sheet_name="Data1", header=None)
    names = [str(v).strip() for v in raw.iloc[0, 1:]]
    dates = pd.to_datetime(raw.iloc[10:, 0], errors="coerce")

    # Labels repeat across original, seasonally adjusted and trend series, so
    # take the first current-price series for each category by position.
    series, seen = {}, set()
    for column, label in enumerate(names):
        if ("Household spending ;" not in label
                or "Current Price" not in label
                or "Percentage" in label):
            continue
        parts = [p.strip() for p in label.split(";")]
        if len(parts) < 2:
            continue
        category = parts[1]
        if category in seen or category.startswith("Total"):
            continue
        seen.add(category)
        values = pd.to_numeric(raw.iloc[10:, column + 1], errors="coerce")
        series[category] = pd.Series(values.values, index=dates)

    frame = pd.DataFrame(series).dropna(how="all")
    frame = frame[frame.index.notna()]
    frame.index = [d.strftime("%Y-%m")
                   for d in frame.index.to_period("M").to_timestamp()]
    return frame.rename(columns=MHSI_NAMES)[CATEGORIES]


def shares(frame: pd.DataFrame) -> pd.DataFrame:
    """Each category as a percentage of that month's total."""
    return frame.div(frame.sum(axis=1), axis=0) * 100


def build_payload() -> dict:
    sim = load_simulation(RESULTS_DIR)
    mhsi = load_mhsi(MHSI_FILE)
    months = [m for m in sim.index if m in mhsi.index]

    sim_share = shares(sim.loc[months])
    mhsi_share = shares(mhsi.loc[months])

    monthly = {}
    for month in months:
        monthly[month] = {
            "sim_total": round(float(sim.loc[month].sum()), 0),
            "mhsi_total": round(float(mhsi.loc[month].sum()), 0),
            "rows": [
                {
                    "category": LABELS[c],
                    "sim_dollars": round(float(sim.loc[month, c]), 0),
                    "sim_pct": round(float(sim_share.loc[month, c]), 2),
                    "mhsi_pct": round(float(mhsi_share.loc[month, c]), 2),
                    "diff": round(float(sim_share.loc[month, c]
                                        - mhsi_share.loc[month, c]), 2),
                }
                for c in CATEGORIES
            ],
            "mae": round(float((sim_share.loc[month]
                                - mhsi_share.loc[month]).abs().mean()), 2),
        }

    covid_months = [m for m in months if COVID_FIRST <= m <= COVID_LAST]
    sim_c = sim_share.loc[covid_months]
    mhsi_c = mhsi_share.loc[covid_months]

    covid = {"months": covid_months, "series": {}}
    for c in CATEGORIES:
        s = sim_c[c]
        a = mhsi_c[c]
        # Correlation of month-on-month change: whether the simulation moves
        # when the benchmark moves, rather than whether it sits at the same
        # level. A series that never moved would score zero by construction.
        corr = float(s.diff().dropna().corr(a.diff().dropna()))
        sim_move = float(s.iloc[-1] - s.iloc[0])
        mhsi_move = float(a.iloc[-1] - a.iloc[0])
        covid["series"][c] = {
            "label": LABELS[c],
            "sim": [round(v, 2) for v in s.tolist()],
            "mhsi": [round(v, 2) for v in a.tolist()],
            "corr": round(corr, 3) if corr == corr else None,
            "sim_move": round(sim_move, 2),
            "mhsi_move": round(mhsi_move, 2),
            "same_direction": bool((sim_move > 0) == (mhsi_move > 0)),
        }

    years = {}
    for month in months:
        years.setdefault(month[:4], []).append(month)

    first = sorted(RESULTS_DIR.glob("households_*.csv"))
    if not first:
        first = sorted((RESULTS_DIR / "decisions").glob("households_*.csv"))

    return {
        "name": STUDENT_NAME,
        "student_id": STUDENT_ID,
        "n_households": int(pd.read_csv(first[0]).shape[0]),
        "years": years,
        "monthly": monthly,
        "covid": covid,
    }


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Household Simulation Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         color: #111; background: #fff; display: flex; min-height: 100vh; }

  /* ---- sidebar ---- */
  aside { width: 280px; flex: 0 0 280px; border-right: 1px solid #000;
          padding: 22px 18px; height: 100vh; overflow-y: auto; position: sticky; top: 0; }
  .who { border-bottom: 1px solid #000; padding-bottom: 14px; margin-bottom: 16px; }
  .who h1 { font-size: 15px; font-weight: 700; }
  .who p { font-size: 13px; margin-top: 2px; }
  .who .meta { font-size: 12px; margin-top: 8px; }

  .nav-head { width: 100%; text-align: left; background: none; border: 1px solid #000;
              padding: 9px 11px; font-size: 14px; font-weight: 600; cursor: pointer;
              margin-bottom: 8px; font-family: inherit; }
  .nav-head:hover { background: #f0f0f0; }
  .nav-head.active { background: #111; color: #fff; }

  .year { margin-left: 4px; }
  .year > button { width: 100%; text-align: left; background: none; border: none;
                   padding: 6px 8px; font-size: 13px; cursor: pointer;
                   border-left: 2px solid #ccc; font-family: inherit; }
  .year > button:hover { background: #f4f4f4; }
  .months { display: none; margin-left: 14px; }
  .months.open { display: block; }
  .months button { width: 100%; text-align: left; background: none; border: none;
                   padding: 4px 8px; font-size: 12.5px; cursor: pointer;
                   border-left: 2px solid #eee; font-family: inherit; }
  .months button:hover { background: #f4f4f4; }
  .months button.sel { background: #111; color: #fff; font-weight: 600; }

  /* ---- main ---- */
  main { flex: 1; padding: 26px 32px 60px; max-width: 1180px; }
  h2 { font-size: 20px; font-weight: 700; margin-bottom: 4px; }
  .sub { font-size: 13px; color: #444; margin-bottom: 22px; }
  h3 { font-size: 15px; font-weight: 700; margin: 26px 0 12px; }

  .totals { display: flex; gap: 20px; margin-bottom: 8px; }
  .total-box { flex: 1; border: 1px solid #000; padding: 16px 18px; }
  .total-box .lab { font-size: 12px; text-transform: uppercase;
                    letter-spacing: .6px; margin-bottom: 6px; }
  .total-box .val { font-size: 26px; font-weight: 700; }
  .total-box .note { font-size: 11.5px; color: #555; margin-top: 4px; }

  .chart { position: relative; height: 330px; margin-bottom: 8px; }
  .chart.tall { height: 400px; }
  .chart.small { height: 210px; }

  table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 6px; }
  th, td { padding: 7px 9px; text-align: right; border-bottom: 1px solid #ddd; }
  th:first-child, td:first-child { text-align: left; }
  th { font-size: 11.5px; text-transform: uppercase; letter-spacing: .5px;
       border-bottom: 1px solid #000; font-weight: 600; }
  tfoot td { border-top: 1px solid #000; border-bottom: none; font-weight: 700; }

  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
          gap: 22px; }
  .panel { border: 1px solid #000; padding: 14px 16px; }
  .panel h4 { font-size: 13.5px; font-weight: 700; margin-bottom: 2px; }
  .panel .dir { font-size: 12px; color: #333; margin-bottom: 8px; }
  .hide { display: none; }
</style>
</head>
<body>

<aside>
  <div class="who">
    <h1 id="who-name"></h1>
    <p id="who-id"></p>
    <div class="meta" id="who-meta"></div>
  </div>

  <button class="nav-head active" id="btn-years">Years</button>
  <div id="year-list"></div>

  <button class="nav-head" id="btn-covid" style="margin-top:14px">
    Correlation during COVID
  </button>
</aside>

<main>
  <section id="view-month">
    <h2 id="m-title">Select a month</h2>
    <div class="sub" id="m-sub">
      Open a year in the sidebar and choose a month.
    </div>
    <div id="m-body" class="hide">
      <div class="totals">
        <div class="total-box">
          <div class="lab">Simulated total</div>
          <div class="val" id="m-sim-total"></div>
          <div class="note" id="m-sim-note"></div>
        </div>
        <div class="total-box">
          <div class="lab">MHSI total</div>
          <div class="val" id="m-mhsi-total"></div>
          <div class="note">ABS, whole of Australia</div>
        </div>
      </div>

      <h3>Share of spending by category</h3>
      <div class="chart tall"><canvas id="m-bars"></canvas></div>

      <h3>The same figures</h3>
      <table id="m-table"></table>
    </div>
  </section>

  <section id="view-covid" class="hide">
    <h2>Correlation during the COVID period</h2>
    <div class="sub" id="c-sub"></div>
    <div class="grid" id="c-grid"></div>
    <h3>Summary</h3>
    <table id="c-table"></table>
  </section>
</main>

<script>
const D = __DATA__;
Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif";
Chart.defaults.font.size = 11;
Chart.defaults.color = "#333";

const MONTH_NAME = ["January","February","March","April","May","June",
                    "July","August","September","October","November","December"];
const pretty = m => MONTH_NAME[parseInt(m.slice(5,7),10)-1] + " " + m.slice(0,4);
const money  = v => "$" + Math.round(v).toLocaleString();

document.getElementById("who-name").textContent = D.name;
document.getElementById("who-id").textContent   = D.student_id;
document.getElementById("who-meta").textContent =
  D.n_households.toLocaleString() + " households \u00b7 " +
  Object.keys(D.monthly).length + " months";

/* ---------- sidebar ---------- */
const yearList = document.getElementById("year-list");
Object.keys(D.years).sort().forEach(y => {
  const wrap = document.createElement("div");
  wrap.className = "year";
  const head = document.createElement("button");
  head.textContent = "\u25b8 " + y;
  const list = document.createElement("div");
  list.className = "months";
  head.onclick = () => {
    const open = list.classList.toggle("open");
    head.textContent = (open ? "\u25be " : "\u25b8 ") + y;
  };
  D.years[y].forEach(m => {
    const b = document.createElement("button");
    b.textContent = pretty(m);
    b.dataset.month = m;
    b.onclick = () => showMonth(m);
    list.appendChild(b);
  });
  wrap.appendChild(head); wrap.appendChild(list);
  yearList.appendChild(wrap);
});

document.getElementById("btn-covid").onclick = showCovid;
document.getElementById("btn-years").onclick = () => {
  document.getElementById("view-covid").classList.add("hide");
  document.getElementById("view-month").classList.remove("hide");
  document.getElementById("btn-covid").classList.remove("active");
  document.getElementById("btn-years").classList.add("active");
};

/* ---------- month view ---------- */
let barChart = null;

function showMonth(month) {
  document.getElementById("view-covid").classList.add("hide");
  document.getElementById("view-month").classList.remove("hide");
  document.getElementById("btn-covid").classList.remove("active");
  document.getElementById("btn-years").classList.add("active");
  document.querySelectorAll(".months button")
          .forEach(b => b.classList.toggle("sel", b.dataset.month === month));

  const d = D.monthly[month];
  document.getElementById("m-title").textContent = pretty(month);
  document.getElementById("m-sub").textContent =
    "Simulated spending against the ABS Monthly Household Spending Indicator. "
    + "Mean absolute error " + d.mae.toFixed(2) + " percentage points.";
  document.getElementById("m-body").classList.remove("hide");

  document.getElementById("m-sim-total").textContent = money(d.sim_total);
  document.getElementById("m-sim-note").textContent =
    D.n_households.toLocaleString() + " simulated households";
  document.getElementById("m-mhsi-total").textContent =
    "$" + Math.round(d.mhsi_total).toLocaleString() + "m";

  const labels = d.rows.map(r => r.category);
  if (barChart) barChart.destroy();
  barChart = new Chart(document.getElementById("m-bars"), {
    type: "bar",
    data: { labels, datasets: [
      { label: "Simulated", data: d.rows.map(r => r.sim_pct),
        backgroundColor: "#111", borderColor: "#111", borderWidth: 1 },
      { label: "MHSI (actual)", data: d.rows.map(r => r.mhsi_pct),
        backgroundColor: "#fff", borderColor: "#111", borderWidth: 1 }
    ]},
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { position: "top", labels: { boxWidth: 12 } } },
      scales: {
        y: { title: { display: true, text: "% of total spending" },
             grid: { color: "#eee" }, border: { color: "#000" } },
        x: { ticks: { maxRotation: 55, minRotation: 40 },
             grid: { display: false }, border: { color: "#000" } }
      }
    }
  });

  let html = "<thead><tr><th>Category</th><th>Simulated $</th>"
           + "<th>Simulated %</th><th>MHSI %</th><th>Difference</th></tr></thead><tbody>";
  d.rows.forEach(r => {
    html += "<tr><td>" + r.category + "</td>"
          + "<td>" + money(r.sim_dollars) + "</td>"
          + "<td>" + r.sim_pct.toFixed(1) + "%</td>"
          + "<td>" + r.mhsi_pct.toFixed(1) + "%</td>"
          + "<td>" + (r.diff > 0 ? "+" : "") + r.diff.toFixed(1) + "</td></tr>";
  });
  html += "</tbody><tfoot><tr><td>Total</td><td>" + money(d.sim_total)
        + "</td><td>100.0%</td><td>100.0%</td><td>MAE " + d.mae.toFixed(2)
        + "</td></tr></tfoot>";
  document.getElementById("m-table").innerHTML = html;
}

/* ---------- covid view ---------- */
let covidCharts = [];

function showCovid() {
  document.getElementById("view-month").classList.add("hide");
  document.getElementById("view-covid").classList.remove("hide");
  document.getElementById("btn-years").classList.remove("active");
  document.getElementById("btn-covid").classList.add("active");
  if (covidCharts.length) return;

  const months = D.covid.months;
  document.getElementById("c-sub").textContent =
    "Each category's share of spending, " + pretty(months[0]) + " to "
    + pretty(months[months.length - 1])
    + ". From the last month before restrictions through to the end of the "
    + "restriction period, the same window the report uses.";

  const grid = document.getElementById("c-grid");
  Object.keys(D.covid.series).forEach(key => {
    const s = D.covid.series[key];
    const panel = document.createElement("div");
    panel.className = "panel";
    const arrowSim  = s.sim_move  > 0 ? "\u2191" : "\u2193";
    const arrowReal = s.mhsi_move > 0 ? "\u2191" : "\u2193";
    panel.innerHTML =
      "<h4>" + s.label + "</h4>"
      + "<div class='dir'>simulated " + arrowSim + " " + Math.abs(s.sim_move).toFixed(1)
      + " pp \u00b7 actual " + arrowReal + " " + Math.abs(s.mhsi_move).toFixed(1)
      + " pp \u00b7 " + (s.same_direction ? "same direction" : "opposite direction")
      + " \u00b7 correlation " + (s.corr === null ? "n/a" : s.corr.toFixed(2)) + "</div>"
      + "<div class='chart small'><canvas></canvas></div>";
    grid.appendChild(panel);

    covidCharts.push(new Chart(panel.querySelector("canvas"), {
      type: "line",
      data: { labels: months, datasets: [
        { label: "Simulated", data: s.sim, borderColor: "#111",
          backgroundColor: "#111", borderWidth: 2, pointRadius: 0, tension: .2 },
        { label: "MHSI", data: s.mhsi, borderColor: "#111",
          borderDash: [5, 4], borderWidth: 2, pointRadius: 0, tension: .2 }
      ]},
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { labels: { boxWidth: 12, usePointStyle: false } } },
        scales: {
          y: { title: { display: true, text: "% of spending" },
               grid: { color: "#eee" }, border: { color: "#000" } },
          x: { ticks: { maxTicksLimit: 8, maxRotation: 50, minRotation: 45 },
               grid: { display: false }, border: { color: "#000" } }
        }
      }
    }));
  });

  let html = "<thead><tr><th>Category</th><th>Simulated change</th>"
           + "<th>Actual change</th><th>Correlation</th></tr>"
           + "</thead><tbody>";
  Object.keys(D.covid.series).forEach(k => {
    const s = D.covid.series[k];
    html += "<tr><td>" + s.label + "</td>"
          + "<td>" + (s.sim_move > 0 ? "+" : "") + s.sim_move.toFixed(1) + " pp</td>"
          + "<td>" + (s.mhsi_move > 0 ? "+" : "") + s.mhsi_move.toFixed(1) + " pp</td>"
          + "<td>" + (s.corr === null ? "n/a" : s.corr.toFixed(2)) + "</td></tr>";
  });
  const vals = Object.values(D.covid.series)
                     .map(s => s.corr).filter(v => v !== null);
  const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
  html += "</tbody><tfoot><tr><td>All categories</td><td></td><td></td><td>"
        + mean.toFixed(2) + "</td></tr></tfoot>";
  document.getElementById("c-table").innerHTML = html;
}
</script>
</body>
</html>
"""


def main() -> None:
    payload = build_payload()
    html = TEMPLATE.replace("__DATA__", json.dumps(payload))
    OUTPUT_FILE.write_text(html, encoding="utf-8")

    print()
    print(f"wrote {OUTPUT_FILE}")
    print(f"  {len(payload['monthly'])} months, "
          f"{payload['n_households']:,} households")
    print(f"  COVID window: {payload['covid']['months'][0]} to "
          f"{payload['covid']['months'][-1]}")

    if OPEN_IN_BROWSER:
        webbrowser.open(OUTPUT_FILE.as_uri())
        print("  opening in your browser")


if __name__ == "__main__":
    main()
