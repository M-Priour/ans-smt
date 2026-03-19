#!/usr/bin/env python3
"""
Scan $expand sur tous les ValueSets actifs du SMT (smt.esante.gouv.fr/fhir)
Génère un rapport Markdown.
"""
import urllib.request, urllib.error, json, sys, re, os
from datetime import datetime, timezone

ERRORS_FILE = os.environ.get("ERRORS_FILE", "errors.json")

SMT = "https://smt.esante.gouv.fr/fhir"
PAGE_SIZE = 100


def fhir_get(url):
    req = urllib.request.Request(url, headers={"Accept": "application/fhir+json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def get_all_valuesets():
    """Récupère tous les ValueSets actifs du SMT via pagination."""
    vs_list = []
    url = f"{SMT}/ValueSet?status=active&_count={PAGE_SIZE}&_elements=id,url,title,status"
    while url:
        bundle = fhir_get(url)
        for entry in bundle.get("entry", []):
            r = entry.get("resource", {})
            vid = r.get("id")
            if vid:
                vs_list.append({
                    "id": vid,
                    "url": r.get("url", ""),
                    "title": r.get("title") or r.get("name") or vid,
                })
        url = None
        for link in bundle.get("link", []):
            if link.get("relation") == "next":
                url = link["url"]
                break
        print(f"  Chargé {len(vs_list)} ValueSets...", file=sys.stderr, end="\r")
    print(f"  Total: {len(vs_list)} ValueSets actifs", file=sys.stderr)
    return vs_list


def expand_valueset(vid):
    """Tente un $expand sur le ValueSet, retourne (ok, total, error_msg)."""
    url = f"{SMT}/ValueSet/{vid}/$expand?_count=1"
    req = urllib.request.Request(url, headers={"Accept": "application/fhir+json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            result = json.load(r)
        if result.get("resourceType") == "OperationOutcome":
            diag = result.get("issue", [{}])[0].get("diagnostics", "unknown")
            return False, 0, diag
        total = result.get("expansion", {}).get("total", 0)
        return True, total, None
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
            diag = body.get("issue", [{}])[0].get("diagnostics", f"HTTP {e.code}")
        except Exception:
            diag = f"HTTP {e.code}"
        return False, 0, diag
    except Exception as ex:
        return False, 0, str(ex)


def extract_codesystem(diag):
    """Extrait l'URL du CodeSystem manquant depuis le message d'erreur."""
    # LOINC not indexed → regrouper
    if "LOINC is not indexed" in diag:
        return "LOINC is not indexed"
    # URL entre guillemets simples (ex: 'http://...')
    m = re.search(r"'(https?://[^']+)'", diag)
    if m:
        return m.group(1)
    # URL dans le message (ex: URL http://... <espace>)
    m = re.search(r'URL\s+(https?://\S+)', diag)
    if m:
        return m.group(1).rstrip('.,)')
    # URL générique — capture jusqu'au premier espace ou ponctuation finale
    m = re.search(r'(https?://[^\s\'"<>]+)', diag)
    if m:
        return m.group(1).rstrip('.,)')
    return diag[:120]


def main():
    print("=== Scan $expand SMT ===", file=sys.stderr)
    print(f"Source: {SMT}", file=sys.stderr)

    vs_list = get_all_valuesets()

    ok_list = []
    error_list = []
    errors_by_cs = {}

    for i, vs in enumerate(vs_list):
        vid = vs["id"]
        success, total, diag = expand_valueset(vid)
        if success:
            ok_list.append({"id": vid, "title": vs["title"], "total": total})
        else:
            cs = extract_codesystem(diag)
            error_list.append({"id": vid, "title": vs["title"], "cs": cs, "diag": diag})
            errors_by_cs.setdefault(cs, []).append(vid)

        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(vs_list)} — OK: {len(ok_list)}, Erreurs: {len(error_list)}",
                  file=sys.stderr)

    print(f"\nFinal — OK: {len(ok_list)}, Erreurs: {len(error_list)}", file=sys.stderr)

    # Génération du rapport Markdown
    scan_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = []
    lines.append("# Rapport $expand — ValueSets SMT ANS")
    lines.append("")
    lines.append(f"**Date :** {scan_date}  ")
    lines.append(f"**Source :** {SMT}  ")
    lines.append(f"**Périmètre :** ValueSets `status=active`")
    lines.append("")
    lines.append("## Résumé")
    lines.append("")
    lines.append("| Statut | Nb |")
    lines.append("|---|---|")
    lines.append(f"| Total ValueSets actifs | {len(vs_list)} |")
    lines.append(f"| ✅ OK | **{len(ok_list)}** |")
    lines.append(f"| ❌ Erreurs | **{len(error_list)}** |")
    lines.append("")

    if errors_by_cs:
        lines.append("## Erreurs par CodeSystem manquant")
        lines.append("")
        lines.append("| CodeSystem | Nb ValueSets |")
        lines.append("|---|---|")
        for cs, ids in sorted(errors_by_cs.items(), key=lambda x: -len(x[1])):
            cs_short = cs.split("/")[-1] if "/" in cs else cs
            lines.append(f"| `{cs_short}` | {len(ids)} |")
        lines.append("")

        lines.append("## Détail des erreurs")
        lines.append("")
        for cs, ids in sorted(errors_by_cs.items(), key=lambda x: -len(x[1])):
            lines.append(f"### `{cs}` ({len(ids)})")
            lines.append("")
            # Récupérer le message d'erreur depuis error_list
            diag_example = next((e["diag"] for e in error_list if e["cs"] == cs), "")
            if diag_example:
                lines.append(f"> {diag_example[:200]}")
                lines.append("")
            for vid in sorted(ids):
                title = next((e["title"] for e in error_list if e["id"] == vid), vid)
                lines.append(f"- [{vid}]({SMT}/ValueSet/{vid}) — {title}")
            lines.append("")

    lines.append("## ValueSets OK")
    lines.append("")
    lines.append("<details><summary>Voir la liste complète</summary>")
    lines.append("")
    lines.append("| ValueSet | Titre | Nb concepts |")
    lines.append("|---|---|---|")
    for v in sorted(ok_list, key=lambda x: x["id"]):
        lines.append(f"| [{v['id']}]({SMT}/ValueSet/{v['id']}) | {v['title']} | {v['total']} |")
    lines.append("")
    lines.append("</details>")

    report = "\n".join(lines)

    output_file = os.environ.get("REPORT_FILE", "rapport-smt-expand.md")
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\nRapport écrit : {output_file}", file=sys.stderr)

    # Export errors.json pour manage_issues.py
    errors_export = {
        "scan_date": scan_date,
        "errors_by_cs": errors_by_cs,
        "diag_by_cs": {
            cs: next((e["diag"] for e in error_list if e["cs"] == cs), "")
            for cs in errors_by_cs
        },
    }
    with open(ERRORS_FILE, "w", encoding="utf-8") as f:
        json.dump(errors_export, f, ensure_ascii=False, indent=2)
    print(f"Erreurs exportées : {ERRORS_FILE}", file=sys.stderr)


if __name__ == "__main__":
    main()
