"""Match ground-truth and prediction slides by content instead of by position.

A prediction that inserts or drops a slide shifts every later slide by one.
Comparing slide i with slide i then reports the whole tail of the deck as
changed. The functions here pair slides by what is on them, keeping deck
order, so an unexpected slide is reported once and the rest still line up.
"""

from collections import Counter

# Cost of leaving a slide without a partner. Pairing two slides always scores
# at least 0, so two slides at the same position stay paired unless leaving
# them apart lets enough later slides match better.
GAP_PENALTY = 0.3

# Position and id fields say where a slide sits, not what is on it.
_IGNORED_KEYS = {"slide_number", "slide_id", "shape_id"}
_WORD_WEIGHT = 2
_SUMMARY_LIMIT = 300


def _slide_tokens(slide):
    """Bag of content tokens for one slide summary from pptx_to_json."""
    tokens = Counter()

    def walk(key, value):
        if isinstance(value, dict):
            for child_key, child in value.items():
                if child_key not in _IGNORED_KEYS:
                    walk(child_key, child)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(key, item)
        elif isinstance(value, str):
            for word in value.split():
                tokens[f"{key}:{word}"] += _WORD_WEIGHT
        elif isinstance(value, bool) or value is None:
            tokens[f"{key}={value}"] += 1
        elif isinstance(value, (int, float)):
            tokens[f"{key}={round(value)}"] += 1

    walk("", slide)
    return tokens


def _similarity(a, b):
    """Weighted Jaccard overlap of two token bags, from 0 to 1."""
    total = sum((a | b).values())
    if not total:
        return 1.0
    return sum((a & b).values()) / total


def align_slides(gt_slides, pred_slides):
    """Pair ground-truth and prediction slides by content, keeping deck order.

    Returns a list of (gt_index, pred_index) tuples that covers every slide of
    both decks exactly once. None marks a slide with no counterpart: (None, j)
    is a slide only the prediction has, (i, None) one only the ground truth has.
    """
    n, m = len(gt_slides), len(pred_slides)
    gt_tokens = [_slide_tokens(slide) for slide in gt_slides]
    pred_tokens = [_slide_tokens(slide) for slide in pred_slides]
    similarity = [[_similarity(g, p) for p in pred_tokens] for g in gt_tokens]

    # Global sequence alignment (Needleman-Wunsch) over slide similarity.
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0] = score[i - 1][0] - GAP_PENALTY
    for j in range(1, m + 1):
        score[0][j] = score[0][j - 1] - GAP_PENALTY
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            score[i][j] = max(
                score[i - 1][j - 1] + similarity[i - 1][j - 1],
                score[i - 1][j] - GAP_PENALTY,
                score[i][j - 1] - GAP_PENALTY,
            )

    pairs = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and score[i][j] == score[i - 1][j - 1] + similarity[i - 1][j - 1]:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif j > 0 and score[i][j] == score[i][j - 1] - GAP_PENALTY:
            pairs.append((None, j - 1))
            j -= 1
        else:
            pairs.append((i - 1, None))
            i -= 1
    pairs.reverse()
    return pairs


def slides_line_up(pairs):
    """True when every slide is paired with the slide at the same position."""
    return all(gt_idx == pred_idx for gt_idx, pred_idx in pairs)


def summarize_slide(slide):
    """One-line description of a slide for the judge: layout and visible text."""
    shapes = slide.get("shapes") or []
    texts = [" ".join(str(shape.get("text") or "").split()) for shape in shapes]
    texts = [text for text in texts if text]
    layout = slide.get("slide_layout")
    prefix = f"layout '{layout}'; " if isinstance(layout, str) and layout else ""
    if not texts:
        return f"{prefix}{len(shapes)} shape(s), no text"
    summary = prefix + "text: " + " | ".join(texts)
    if len(summary) > _SUMMARY_LIMIT:
        summary = summary[: _SUMMARY_LIMIT - 3] + "..."
    return summary


def visual_slide_pairs(gt_json, pred_json, gt_image_count, pred_image_count):
    """Content-matched slide pairs for the visual judge.

    Returns None when pairing by position is already right or is the only safe
    option: the decks line up, a deck summary is missing, or the rendered
    image counts do not match the slide counts (so indices cannot be trusted).
    """
    if not isinstance(gt_json, dict) or not isinstance(pred_json, dict):
        return None
    gt_slides = gt_json.get("slides") or []
    pred_slides = pred_json.get("slides") or []
    if len(gt_slides) != gt_image_count or len(pred_slides) != pred_image_count:
        return None
    pairs = align_slides(gt_slides, pred_slides)
    if slides_line_up(pairs):
        return None
    return pairs
