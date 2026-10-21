"""Summarise an OWASP ZAP JSON report into docs/results/zap_summary.json (alerts per target, by risk).

  python scripts/summarize_zap.py zap-report.json [--out docs/results/zap_summary.json]
"""

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

ap = argparse.ArgumentParser()
ap.add_argument("report", type=Path)
ap.add_argument("--out", type=Path, default=Path("docs/results/zap_summary.json"))
a = ap.parse_args()
report = json.loads(a.report.read_text())
targets: dict[str, dict] = {}
for site in report["site"]:
    for alert in site["alerts"]:
        risk = alert["riskdesc"].split(" ")[0]
        for inst in alert["instances"]:
            host = urlparse(inst["uri"]).netloc
            t = targets.setdefault(host, {"by_risk": Counter(), "alerts": set()})
            t["by_risk"][risk] += 1
            t["alerts"].add(f"{risk}: {alert['alert']}")
out = {
    "generated_at": datetime.now(UTC).isoformat(),
    "tool": f"OWASP ZAP {report.get('@version', '')} zap-api-scan (OpenAPI)",
    "targets": {h: {"by_risk": dict(v["by_risk"]), "alerts": sorted(v["alerts"])} for h, v in targets.items()},
}
a.out.parent.mkdir(parents=True, exist_ok=True)
a.out.write_text(json.dumps(out, indent=2) + "\n")
print(json.dumps(out, indent=2))
