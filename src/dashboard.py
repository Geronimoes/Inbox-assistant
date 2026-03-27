"""
Weekly email dashboard generator.

Reads data/weekly-stats.json (rolling 28-day window) and produces
dashboard/index.html — a static page with Chart.js charts showing:
  - Emails per day (stacked bar: urgent / action / fyi / noise)
  - 28-day category totals (pie chart)
  - Draft reply count over time
  - Key summary numbers

Serve with Caddy (see cron/caddy-dashboard.snippet) behind
Cloudflare Access or another auth layer.

Usage:
    python src/dashboard.py          # generate dashboard/index.html
    python src/dashboard.py --open   # generate and open in browser (local only)
"""

import argparse
import json
import sys
from datetime import datetime, date, timedelta
from pathlib import Path


def load_stats(stats_file: Path) -> list[dict]:
    """Load the rolling stats JSON. Returns [] if file doesn't exist."""
    if not stats_file.exists():
        return []
    try:
        data = json.loads(stats_file.read_text())
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def last_n_days(stats: list[dict], n: int = 28) -> list[dict]:
    """Return stats for the last N calendar days, filling gaps with zeros."""
    today = date.today()
    by_date = {s["date"]: s for s in stats if "date" in s}
    result = []
    for i in range(n - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        result.append(by_date.get(d, {
            "date": d, "urgent": 0, "action": 0, "fyi": 0,
            "noise": 0, "total": 0, "drafts": 0,
        }))
    return result


def generate_html(stats_file: Path) -> str:
    """Generate the full dashboard HTML string."""
    all_stats = load_stats(stats_file)
    days = last_n_days(all_stats, 28)

    labels    = [d["date"][5:] for d in days]   # MM-DD
    urgent    = [d.get("urgent", 0)  for d in days]
    action    = [d.get("action", 0)  for d in days]
    fyi       = [d.get("fyi", 0)     for d in days]
    noise     = [d.get("noise", 0)   for d in days]
    drafts    = [d.get("drafts", 0)  for d in days]

    total_urgent = sum(urgent)
    total_action = sum(action)
    total_fyi    = sum(fyi)
    total_noise  = sum(noise)
    total_all    = total_urgent + total_action + total_fyi + total_noise
    total_drafts = sum(drafts)

    active_days = sum(1 for d in days if d.get("total", 0) > 0)
    avg_per_day = round(total_all / active_days, 1) if active_days else 0

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Inbox Assistant — Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: #f8fafc; color: #1e293b; padding: 24px;
    }}
    h1 {{ font-size: 22px; margin-bottom: 4px; }}
    .subtitle {{ color: #64748b; font-size: 13px; margin-bottom: 28px; }}
    .stats-row {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 12px; margin-bottom: 28px;
    }}
    .stat-card {{
      background: #fff; border-radius: 8px; padding: 16px 20px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.07);
    }}
    .stat-card .label {{ font-size: 12px; color: #64748b; margin-bottom: 4px; }}
    .stat-card .value {{ font-size: 28px; font-weight: 700; }}
    .stat-card.urgent .value {{ color: #dc2626; }}
    .stat-card.action .value {{ color: #d97706; }}
    .stat-card.fyi    .value {{ color: #2563eb; }}
    .stat-card.drafts .value {{ color: #16a34a; }}
    .charts {{
      display: grid; grid-template-columns: 2fr 1fr;
      gap: 20px; margin-bottom: 28px;
    }}
    @media (max-width: 700px) {{ .charts {{ grid-template-columns: 1fr; }} }}
    .chart-card {{
      background: #fff; border-radius: 8px; padding: 20px;
      box-shadow: 0 1px 3px rgba(0,0,0,0.07);
    }}
    .chart-card h2 {{ font-size: 14px; color: #475569; margin-bottom: 16px; }}
    .footer {{ color: #94a3b8; font-size: 12px; margin-top: 8px; }}
  </style>
</head>
<body>

<h1>📬 Inbox Assistant</h1>
<p class="subtitle">Last 28 days · Generated {generated_at}</p>

<div class="stats-row">
  <div class="stat-card">
    <div class="label">Total emails</div>
    <div class="value">{total_all}</div>
  </div>
  <div class="stat-card urgent">
    <div class="label">⚡ Urgent</div>
    <div class="value">{total_urgent}</div>
  </div>
  <div class="stat-card action">
    <div class="label">📋 Action</div>
    <div class="value">{total_action}</div>
  </div>
  <div class="stat-card fyi">
    <div class="label">🔵 FYI</div>
    <div class="value">{total_fyi}</div>
  </div>
  <div class="stat-card drafts">
    <div class="label">✎ Drafts written</div>
    <div class="value">{total_drafts}</div>
  </div>
  <div class="stat-card">
    <div class="label">Avg emails/active day</div>
    <div class="value">{avg_per_day}</div>
  </div>
</div>

<div class="charts">
  <div class="chart-card">
    <h2>Emails per day (last 28 days)</h2>
    <canvas id="barChart" height="180"></canvas>
  </div>
  <div class="chart-card">
    <h2>Category breakdown</h2>
    <canvas id="pieChart" height="180"></canvas>
  </div>
</div>

<div class="chart-card" style="margin-bottom: 28px;">
  <h2>Draft replies written per day</h2>
  <canvas id="draftChart" height="80"></canvas>
</div>

<p class="footer">Inbox Assistant · data refreshed on each morning run</p>

<script>
const labels = {json.dumps(labels)};
const urgent = {json.dumps(urgent)};
const action = {json.dumps(action)};
const fyi    = {json.dumps(fyi)};
const noise  = {json.dumps(noise)};
const drafts = {json.dumps(drafts)};

// Stacked bar chart
new Chart(document.getElementById('barChart'), {{
  type: 'bar',
  data: {{
    labels,
    datasets: [
      {{ label: '⚡ Urgent', data: urgent, backgroundColor: '#fca5a5' }},
      {{ label: '📋 Action', data: action, backgroundColor: '#fcd34d' }},
      {{ label: '🔵 FYI',   data: fyi,    backgroundColor: '#93c5fd' }},
      {{ label: '⚪ Noise', data: noise,  backgroundColor: '#e2e8f0' }},
    ]
  }},
  options: {{
    responsive: true,
    scales: {{
      x: {{ stacked: true, ticks: {{ maxTicksLimit: 14 }} }},
      y: {{ stacked: true, beginAtZero: true }}
    }},
    plugins: {{ legend: {{ position: 'bottom' }} }}
  }}
}});

// Pie chart
new Chart(document.getElementById('pieChart'), {{
  type: 'doughnut',
  data: {{
    labels: ['Urgent', 'Action', 'FYI', 'Noise'],
    datasets: [{{
      data: [{total_urgent}, {total_action}, {total_fyi}, {total_noise}],
      backgroundColor: ['#fca5a5', '#fcd34d', '#93c5fd', '#e2e8f0'],
      borderWidth: 1,
    }}]
  }},
  options: {{
    plugins: {{ legend: {{ position: 'bottom' }} }}
  }}
}});

// Draft line chart
new Chart(document.getElementById('draftChart'), {{
  type: 'bar',
  data: {{
    labels,
    datasets: [{{
      label: 'Drafts',
      data: drafts,
      backgroundColor: '#86efac',
    }}]
  }},
  options: {{
    responsive: true,
    scales: {{
      x: {{ ticks: {{ maxTicksLimit: 14 }} }},
      y: {{ beginAtZero: true, ticks: {{ stepSize: 1 }} }}
    }},
    plugins: {{ legend: {{ display: false }} }}
  }}
}});
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Generate inbox dashboard HTML")
    parser.add_argument("--open", action="store_true",
                        help="Open the generated file in the default browser")
    args = parser.parse_args()

    project_root = Path(__file__).parent.parent
    stats_file   = project_root / "data" / "weekly-stats.json"

    # Allow a custom output path via config.yaml (dashboard.output_path).
    # Defaults to dashboard/index.html inside the project root.
    output_file  = project_root / "dashboard" / "index.html"
    config_path  = project_root / "config.yaml"
    if config_path.exists():
        import yaml
        cfg = yaml.safe_load(config_path.read_text())
        custom_path = cfg.get("dashboard", {}).get("output_path", "")
        if custom_path:
            output_file = Path(custom_path).expanduser()

    output_file.parent.mkdir(parents=True, exist_ok=True)

    html = generate_html(stats_file)
    output_file.write_text(html, encoding="utf-8")
    print(f"✓ Dashboard written: {output_file}")

    if not load_stats(stats_file):
        print("  (No stats data yet — run fetch_and_triage.py at least once "
              "to populate data/weekly-stats.json)")

    if args.open:
        import webbrowser
        webbrowser.open(output_file.as_uri())


if __name__ == "__main__":
    main()
