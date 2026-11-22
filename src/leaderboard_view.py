"""Shape leaderboard data for the leaderboard page: one summary per base split.

`app.get_leaderboard_data()` returns, per base split, an "All" view followed by one view
per edit category. The page shows a single ranking per split with each system's category
scores beside it, so this module folds the category views into the rows of the "All" view.
"""
import re
from collections import Counter


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _note(entry, common_judge):
    """The one exception worth showing under a system's name, or ""."""
    if entry["scored_cases"] < entry["expected_cases"]:
        return f"{entry['scored_cases']} of {entry['expected_cases']} cases scored"
    if entry["judge"] != common_judge:
        return entry["judge"]
    return ""


def summarize(leaderboard_data):
    """[{key, label, case_count, categories, entries}] in the order of the base splits."""
    splits = []
    for group in leaderboard_data["groups"]:
        all_view, category_views = group["views"][0], group["views"][1:]
        by_category = [{e["name"]: e["score_pct"] for e in view["entries"]} for view in category_views]
        judges = Counter(e["judge"] for e in all_view["entries"])
        common_judge = judges.most_common(1)[0][0] if judges else ""
        top = max((e["score_pct"] for e in all_view["entries"]), default=0)
        entries = []
        for rank, e in enumerate(all_view["entries"], 1):
            entries.append({
                "id": _slug(e["name"]),
                "rank": rank,
                "name": e["name"],
                "short": e.get("chart_label") or e["name"],
                "brand": e.get("brand", ""),
                "family": _slug(e.get("brand", "")),      # picks the color its bar and dots wear
                "provider": e.get("provider", ""),
                "icon": e.get("icon", e["name"][:1]),
                "color": e.get("color", "#6e6e6e"),
                "score": e["score_pct"],
                "bar": 100 * e["score_pct"] / top if top else 0.0,     # bar height, % of the tallest bar
                "instruction": e["if_pct"],
                "visual": e["vq_pct"],
                "edit_minutes": None if e.get("edit_time_s") is None else e["edit_time_s"] / 60,
                "edit_display": e.get("edit_time_display") or "",
                "note": _note(e, common_judge),
                "category_scores": [scores.get(e["name"]) for scores in by_category],
            })
        splits.append({
            "key": group["base"],
            "label": group["label"],
            "case_count": all_view["case_count"],
            "categories": [{"label": v["label"], "case_count": v["case_count"]} for v in category_views],
            "entries": entries,
        })
    return splits


def case_strip(entries, case_scores, case_names):
    """Per-case scores for one split: {cases, rows}, cases ordered from easiest to hardest.

    `case_scores` maps a system's name to {case name: score}. A case a system never scored
    stays None in its row and counts as 0 toward the case's mean, as it does in the ranking.
    """
    def mean(case):
        return sum(case_scores.get(e["name"], {}).get(case) or 0 for e in entries) / max(len(entries), 1)

    cases = sorted(case_names, key=lambda case: (-mean(case), case))
    rows = [{"id": e["id"], "name": e["name"],
             "scores": [case_scores.get(e["name"], {}).get(case) for case in cases]} for e in entries]
    return {"cases": cases, "rows": rows}
