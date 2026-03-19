#!/usr/bin/env python3
"""
Crée/met à jour/ferme les GitHub Issues à partir du rapport JSON d'erreurs.
Nécessite : GITHUB_TOKEN, GITHUB_REPOSITORY (owner/repo)
"""
import json, os, sys, re
import urllib.request, urllib.error

GITHUB_API = "https://api.github.com"
TOKEN = os.environ["GITHUB_TOKEN"]
REPO = os.environ["GITHUB_REPOSITORY"]  # ex: M-Priour/smt-expand-report
ERRORS_FILE = os.environ.get("ERRORS_FILE", "errors.json")
LABEL_PREFIX = "codesystem:"
LABEL_BOT = "smt-scan"


def gh(method, path, data=None):
    url = f"{GITHUB_API}{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r) if r.length != 0 else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  GitHub API error {e.code}: {body[:200]}", file=sys.stderr)
        return None


def ensure_label(name, color="d93f0b", description=""):
    existing = gh("GET", f"/repos/{REPO}/labels/{urllib.parse.quote(name)}")
    if existing is None:
        gh("POST", f"/repos/{REPO}/labels", {"name": name, "color": color, "description": description})


def get_open_issues():
    """Retourne toutes les issues ouvertes avec le label smt-scan."""
    issues = {}
    page = 1
    while True:
        result = gh("GET", f"/repos/{REPO}/issues?labels={LABEL_BOT}&state=open&per_page=100&page={page}")
        if not result:
            break
        for issue in result:
            issues[issue["number"]] = issue
        if len(result) < 100:
            break
        page += 1
    return issues


def issue_title(cs):
    cs_short = cs.split("/")[-1] if "/" in cs else cs
    return f"[SMT] CodeSystem manquant : `{cs_short}`"


def issue_body(cs, vs_ids, diag_example, scan_date):
    lines = [
        f"## CodeSystem manquant : `{cs}`",
        "",
        f"**Détecté le :** {scan_date}  ",
        f"**Message d'erreur :**",
        f"> {diag_example[:300]}",
        "",
        f"## ValueSets affectés ({len(vs_ids)})",
        "",
    ]
    for vid in sorted(vs_ids):
        lines.append(f"- [ ] [{vid}](https://smt.esante.gouv.fr/fhir/ValueSet/{vid})")
    lines += [
        "",
        "---",
        f"*Issue créée automatiquement par le workflow SMT Expand Scan*",
    ]
    return "\n".join(lines)


def main():
    import urllib.parse

    with open(ERRORS_FILE, encoding="utf-8") as f:
        data = json.load(f)

    errors_by_cs = data["errors_by_cs"]   # {cs_url: [vs_id, ...]}
    diag_by_cs = data["diag_by_cs"]       # {cs_url: diag_example}
    scan_date = data["scan_date"]

    # Assurer que les labels existent
    ensure_label(LABEL_BOT, color="0075ca", description="Issue créée par le SMT expand scan")
    ensure_label("licence", color="e4e669", description="CodeSystem sous licence")
    ensure_label("fragment", color="cfd3d7", description="CodeSystem à créer en fragment")

    # Récupérer les issues existantes
    open_issues = get_open_issues()
    # Index par titre
    issues_by_title = {i["title"]: i for i in open_issues.values()}

    current_cs = set(errors_by_cs.keys())
    processed = set()

    for cs, vs_ids in errors_by_cs.items():
        title = issue_title(cs)
        diag = diag_by_cs.get(cs, "")
        processed.add(title)

        if title in issues_by_title:
            # Issue existante → ajouter un commentaire avec le diff
            issue = issues_by_title[title]
            issue_num = issue["number"]

            # Extraire les VS déjà listés dans l'issue
            body = issue.get("body", "")
            existing_vs = set(re.findall(r'\[([^\]]+)\]\(https://smt\.esante\.gouv\.fr/fhir/ValueSet/[^\)]+\)', body))
            new_vs = set(vs_ids) - existing_vs
            resolved_vs = existing_vs - set(vs_ids)

            comment_lines = [f"## Mise à jour — {scan_date}", ""]
            if new_vs:
                comment_lines.append(f"### 🆕 Nouveaux ValueSets en erreur ({len(new_vs)})")
                for v in sorted(new_vs):
                    comment_lines.append(f"- [ ] [{v}](https://smt.esante.gouv.fr/fhir/ValueSet/{v})")
                comment_lines.append("")
            if resolved_vs:
                comment_lines.append(f"### ✅ ValueSets résolus ({len(resolved_vs)})")
                for v in sorted(resolved_vs):
                    comment_lines.append(f"- [x] {v}")
                comment_lines.append("")
            if not new_vs and not resolved_vs:
                comment_lines.append("Aucun changement depuis le dernier scan.")

            gh("POST", f"/repos/{REPO}/issues/{issue_num}/comments",
               {"body": "\n".join(comment_lines)})
            print(f"  Commentaire ajouté sur #{issue_num} : {title}")

        else:
            # Créer une nouvelle issue
            body = issue_body(cs, vs_ids, diag, scan_date)
            labels = [LABEL_BOT]
            # Heuristique label
            if any(x in cs for x in ["icd", "atc", "ccam", "adicap", "tccr", "ncit"]):
                labels.append("licence")
            elif "ucum" in cs.lower() or "unitsofmeasure" in cs.lower():
                labels.append("fragment")

            result = gh("POST", f"/repos/{REPO}/issues", {
                "title": title,
                "body": body,
                "labels": labels,
            })
            if result:
                print(f"  Issue #{result['number']} créée : {title}")

    # Fermer les issues dont le CS n'est plus en erreur
    for title, issue in issues_by_title.items():
        if title not in processed:
            issue_num = issue["number"]
            gh("PATCH", f"/repos/{REPO}/issues/{issue_num}", {"state": "closed"})
            gh("POST", f"/repos/{REPO}/issues/{issue_num}/comments", {
                "body": f"✅ Toutes les erreurs liées à ce CodeSystem sont résolues (scan du {scan_date}). Issue fermée automatiquement."
            })
            print(f"  Issue #{issue_num} fermée : {title}")

    print("Gestion des issues terminée.", file=sys.stderr)


if __name__ == "__main__":
    main()
