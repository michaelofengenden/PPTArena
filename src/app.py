# app.py
import os
import json
import shutil
from flask import Flask, request, jsonify, render_template, send_from_directory, redirect, url_for
from flask_cors import CORS
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
import progress
import llm_handler 
import re 
from pathlib import Path 
import time
import csv
from datetime import datetime
import uuid
import sys
import tempfile
import threading
import queue

# Import from new modules
from ppt import (
    pptx_to_json,
    convert_pptx_to_pdf,
    export_slides_to_images,
    # image_to_base64, # Not directly exported from ppt/__init__.py, need to check
    extract_specific_xml_from_pptx
)

def image_to_base64(image_path):
    """Converts an image file to a base64 encoded string."""
    try:
        import base64
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')
    except Exception as e:
        print(f"Error converting image {image_path} to base64: {e}")
        return None

# --- Environment Detection ---
IS_GUNICORN = "gunicorn" in sys.modules

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
CORS(app)

# --- Disable Caching for Development ---
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# --- MODIFIED: Configuration ---
# UPLOAD_FOLDER is removed, as we now reference the benchmark ppts directly
SCRIPT_DIR = Path(__file__).parent.resolve()
DATA_DIR = SCRIPT_DIR / "work_dir"
USER_UPLOADS_FOLDER = DATA_DIR / "user_uploads"
SESSIONS_FOLDER = DATA_DIR / "sessions"
TSBENCH_PRESENTATIONS_DIR = SCRIPT_DIR / "TSBench" / "benchmark_ppts"
EXTRACTED_XML_FOLDER = DATA_DIR / 'extracted_xml_original'
MODIFIED_PPTX_FOLDER = DATA_DIR / 'modified_ppts'
GENERATED_IMAGES_FOLDER = DATA_DIR / 'generated_images'
GENERATED_PDFS_FOLDER = DATA_DIR / 'generated_pdfs'
PROCESSING_LOG_CSV = SCRIPT_DIR / 'processing_log.csv'
EVALUATION_RESULTS_CSV = SCRIPT_DIR / 'evaluation_results.csv'


ALLOWED_EXTENSIONS = {'pptx'}

# --- MODIFIED: Use Path objects for consistency ---
app.config['EXTRACTED_XML_FOLDER'] = str(EXTRACTED_XML_FOLDER)
app.config['MODIFIED_PPTX_FOLDER'] = str(MODIFIED_PPTX_FOLDER)
app.config['GENERATED_IMAGES_FOLDER'] = str(GENERATED_IMAGES_FOLDER)
app.config['GENERATED_PDFS_FOLDER'] = str(GENERATED_PDFS_FOLDER)
app.config['TSBENCH_PRESENTATIONS_DIR'] = str(TSBENCH_PRESENTATIONS_DIR)
app.config['USER_UPLOADS_FOLDER'] = str(USER_UPLOADS_FOLDER)
app.config['SESSIONS_FOLDER'] = str(SESSIONS_FOLDER)

# --- MODIFIED: Create only necessary directories ---
for folder in [DATA_DIR, EXTRACTED_XML_FOLDER, MODIFIED_PPTX_FOLDER, GENERATED_IMAGES_FOLDER, GENERATED_PDFS_FOLDER, USER_UPLOADS_FOLDER, SESSIONS_FOLDER]:
    folder.mkdir(parents=True, exist_ok=True)


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def check_api_key(model_id, api_keys):
    """
    Verifies if the required API key is present for the selected model.
    Returns (True, None) if valid, or (False, error_message) if invalid.
    """
    if not model_id:
        return False, "No model selected."
    
    # Import here to avoid circular dependency if placed at top, 
    # though app.py imports llm_handler which imports utils, so it should be fine.
    # But let's use the logic directly or import from llm.utils if available.
    # We'll use a simple check here to match frontend and avoid complex imports inside helper.
    is_openai = any(token in model_id.lower() for token in ["gpt", "openai", "o1", "o3", "o4", "gpt-5"])
    
    if is_openai:
        if not api_keys.get("openai"):
            return False, f"OpenAI API Key is required for model '{model_id}'."
    else:
        # Assume Gemini/Google
        if not api_keys.get("gemini"):
            return False, f"Gemini API Key is required for model '{model_id}'."
            
    return True, None

def generate_pdf_preview_url(pptx_path):
    """Convert pptx to PDF and return the relative /view_pdf URL if available."""
    try:
        pdf_path = convert_pptx_to_pdf(str(pptx_path), app.config['GENERATED_PDFS_FOLDER'])
        if pdf_path:
            return f"/view_pdf/{Path(pdf_path).name}"
    except Exception as e:
        app.logger.error(f"Failed to generate PDF preview for {pptx_path}: {e}", exc_info=True)
    return None

def log_processing_details(log_data):
    """Appends a record to the processing log CSV file."""
    file_exists = os.path.isfile(PROCESSING_LOG_CSV)
    with open(PROCESSING_LOG_CSV, 'a', newline='') as csvfile:
        fieldnames = [
            'Timestamp', 'OriginalFilename', 'LLMEngineUsed', 
            'TotalProcessingTimeSeconds', 'JSONExtractionTimeSeconds', 
            'XMLExtractionTimeSeconds', 'LLMInferenceTimeSeconds', 
            'PPTXModificationTimeSeconds', 'ImageConversionTimeSeconds',
            'TotalSlidesInOriginal', 'NumberOfSlidesEditedByLLM', 
            'ModifiedXMLFilesList'
        ]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        
        if not file_exists:
            writer.writeheader()
        
        writer.writerow(log_data)

def _get_slide_number_from_path(filepath):
    """Extracts the slide number from an image path like '.../slide-12.png'."""
    # The last number in the filename is assumed to be the slide number.
    # e.g., 'slide-0001-5.png' -> 5
    numbers = re.findall(r'\d+', Path(filepath).name)
    if numbers:
        return int(numbers[-1])
    return 0

@app.route('/')
def index():
    """Redirect to the evaluation page by default."""
    return redirect(url_for('evaluation_page'))

def stream_long_task(app_context, task_func, *args, **kwargs):
    """
    Runs a long-running function in a background thread and yields spaces 
    to keep the connection alive (preventing proxy timeouts).
    """
    q = queue.Queue()

    def run_task():
        with app_context:
            try:
                result = task_func(*args, **kwargs)
                q.put({"status": "success", "result": result})
            except Exception as e:
                app.logger.error(f"Error in background task: {e}", exc_info=True)
                q.put({"status": "error", "error": str(e)})

    thread = threading.Thread(target=run_task)
    thread.start()

    def generate():
        while True:
            try:
                # Wait for up to 10 seconds for the task to complete
                msg = q.get(timeout=10)
                if msg["status"] == "success":
                    yield json.dumps(msg["result"])
                    break
                else:
                    yield json.dumps({"error": msg["error"]})
                    break
            except queue.Empty:
                # Yield a space to keep the connection alive
                yield " "

    from flask import Response
    return Response(generate(), mimetype='application/json')

# Earlier-era systems (Gemini 3.1 Pro / GPT-5.2 judges, mixed splits), parked
# while the leaderboard shows only the Kimi-K2.6-judged hard-subset cohort.
# Move an entry back into LEADERBOARD_SOURCES below to restore it.
LEADERBOARD_SOURCES_ARCHIVED = [
    {
        "name": "PPTPilot (Gemini 3.1 Pro)",
        "model": "Gemini 3.1 Pro",
        "provider": "PPTPilot",
        "brand": "PPTPilot",
        "icon": "P",
        "color": "#2563eb",
        "split": "full",
        "expected_cases": 100,
        "path": SCRIPT_DIR.parent / "evaluation_results_bulk.json",
        "judge": "Gemini 3.1 Pro judge",
        "include_subset": True,
    },
    {
        "name": "PPTPilot (GPT-5.2)",
        "model": "GPT-5.2",
        "provider": "PPTPilot",
        "brand": "PPTPilot",
        "icon": "P",
        "color": "#0d9488",
        "split": "full",
        "expected_cases": 100,
        "path": SCRIPT_DIR / "Main Results" / "evaluation_results_rejudged.csv",
        "judge": "GPT-5.2 judge",
        "include_subset": True,
    },
    {
        "name": "ChatGPT",
        "model": "ChatGPT",
        "provider": "OpenAI",
        "brand": "OpenAI",
        "icon": "◎",
        "color": "#10a37f",
        "split": "full",
        "expected_cases": 100,
        "path": SCRIPT_DIR / "Main Results" / "chatgpt_judge_results.csv",
        "judge": "GPT-5.2 judge",
    },
    {
        "name": "Gemini CLI",
        "model": "Gemini 3.1 Pro",
        "provider": "Google",
        "brand": "Gemini",
        "icon": "G",
        "color": "#4285f4",
        "split": "subset",
        "expected_cases": 25,
        "path": SCRIPT_DIR / "benchmark_runs" / "gemini_cli_subset25_rejudge_gpt51.csv",
        "judge": "GPT-5.2 judge",
        # This run covers a slightly different 25 (includes "Case 100", missing
        # "Case 21"), so it must not redefine the canonical matched subset.
        "defines_subset": False,
    },
]

LEADERBOARD_SOURCES = [
    # --- agent_bench cohort (see agent_bench/README.md) ---
    # Pre-registered sources: each entry appears on the leaderboard automatically
    # once its judged CSV lands in agent_bench/results/. The Codex run defines
    # the canonical matched subset (subset25).
    {
        "name": "Codex (GPT-5.5 xhigh)",
        "model": "GPT-5.5 xhigh",
        "provider": "OpenAI",
        "brand": "OpenAI",
        "icon": "C",
        "color": "#1a7f64",
        "chart_label": "GPT-5.5",
        "split": "subset",
        "expected_cases": 25,
        "path": SCRIPT_DIR.parent / "agent_bench" / "results" / "codex_gpt55_judge_results.csv",
        "judge": "Kimi K2.6 judge",
        "defines_subset": True,
    },
    {
        "name": "Claude Code (Opus 4.8)",
        "model": "Claude Opus 4.8",
        "provider": "Anthropic",
        "brand": "Claude",
        "icon": "✶",
        "color": "#b05730",
        "chart_label": "Claude 4.8",
        "split": "subset",
        "expected_cases": 25,
        "path": SCRIPT_DIR.parent / "agent_bench" / "results" / "claude_code_opus48_judge_results.csv",
        "judge": "Kimi K2.6 judge",
        "defines_subset": False,
    },
    {
        "name": "OpenCode (GLM-5.2)",
        "model": "GLM-5.2",
        "provider": "Zhipu AI · OpenCode",
        "brand": "GLM",
        "icon": "G",
        "color": "#1d4ed8",
        "chart_label": "GLM-5.2",
        "split": "subset",
        "expected_cases": 25,
        "path": SCRIPT_DIR.parent / "agent_bench" / "results" / "opencode_glm52_judge_results.csv",
        "judge": "Kimi K2.6 judge",
        "defines_subset": False,
    },
    {
        "name": "Gemini CLI (3.5 Flash)",
        "model": "Gemini 3.5 Flash",
        "provider": "Google",
        "brand": "Gemini",
        "icon": "G",
        "color": "#669df6",
        "chart_label": "Gemini 3.5",
        "split": "subset",
        "expected_cases": 25,
        "path": SCRIPT_DIR.parent / "agent_bench" / "results" / "gemini_cli_35flash_judge_results.csv",
        "judge": "Kimi K2.6 judge",
        "defines_subset": False,
    },
    {
        "name": "OpenCode (MiniMax-M3)",
        "model": "MiniMax-M3",
        "provider": "MiniMax · OpenCode",
        "brand": "MiniMax",
        "icon": "M",
        "color": "#8b7cfd",
        "chart_label": "MiniMax-M3",
        "split": "subset",
        "expected_cases": 25,
        "path": SCRIPT_DIR.parent / "agent_bench" / "results" / "opencode_minimax_m3_judge_results.csv",
        "judge": "Kimi K2.6 judge",
        "defines_subset": False,
    },
    {
        "name": "OpenCode (DeepSeek V4 Pro)",
        "model": "DeepSeek V4 Pro",
        "provider": "DeepSeek · OpenCode",
        "brand": "DeepSeek",
        "icon": "D",
        "color": "#4d6bfe",
        "chart_label": "DeepSeek V4",
        "split": "subset",
        "expected_cases": 25,
        "path": SCRIPT_DIR.parent / "agent_bench" / "results" / "opencode_deepseek_v4_judge_results.csv",
        "judge": "Kimi K2.6 judge",
        "defines_subset": False,
    },
    {
        "name": "OpenCode (Kimi K2.7 Code)",
        "model": "Kimi K2.7 Code",
        "provider": "Moonshot AI · OpenCode",
        "brand": "Kimi",
        "icon": "K",
        "color": "#16181d",
        "chart_label": "Kimi K2.7",
        "split": "subset",
        "expected_cases": 25,
        "path": SCRIPT_DIR.parent / "agent_bench" / "results" / "opencode_kimi_k27code_judge_results.csv",
        "judge": "Kimi K2.6 judge",
        "defines_subset": False,
    },
    # --- External full-set agents ---
    {
        "name": "Baidu DuMate",
        "model": "PPTX Skill r266 · Moonshot native",
        "provider": "Baidu",
        "brand": "Baidu",
        "icon": "D",
        "color": "#5869E8",
        "chart_label": "DuMate",
        "split": "subset",
        "expected_cases": 25,
        "path": SCRIPT_DIR.parent / "agent_bench" / "results" / "baidu_dumate_pptx_skill_r266_judge_results.csv",
        "judge": "Kimi K2.6 judge",
        "defines_subset": False,
    },
    # (PPTPilot Kimi/Gemma pilot rows removed from the board — scored too low to
    # be informative; their CSVs remain in agent_bench/results/ if ever restored.)
    # --- CUA (computer-use agent) cohort ---
    # Product agents driving a browser/desktop rather than a CLI; judged in an
    # earlier era, labels kept truthful. None redefine the canonical subset.
    {
        "name": "Claude (CUA)",
        "model": "Claude 3.7 Sonnet",
        "provider": "Anthropic",
        "brand": "Claude",
        "icon": "✶",
        "color": "#d97757",
        "chart_label": "Claude CUA",
        "split": "subset",
        "expected_cases": 25,
        "path": SCRIPT_DIR.parent / "agent_bench" / "results" / "cua_claude37_judge_results.csv",
        "judge": "Kimi K2.6 judge",
        "defines_subset": False,
        "subset_only": True,
    },
    {
        "name": "ChatGPT Agent (CUA)",
        "model": "ChatGPT Agent",
        "provider": "OpenAI",
        "brand": "OpenAI",
        "icon": "◎",
        "color": "#0f0f0f",
        "chart_label": "ChatGPT CUA",
        "split": "subset",
        "expected_cases": 25,
        "path": SCRIPT_DIR.parent / "agent_bench" / "results" / "cua_chatgpt_agent_judge_results.csv",
        "judge": "Kimi K2.6 judge",
        "defines_subset": False,
        "subset_only": True,
    },
    {
        "name": "MiniMax Agent (CUA)",
        "model": "MiniMax Agent",
        "provider": "MiniMax",
        "brand": "MiniMax",
        "icon": "M",
        "color": "#6d5dfc",
        "chart_label": "MiniMax CUA",
        "split": "subset",
        "expected_cases": 25,
        "path": SCRIPT_DIR.parent / "agent_bench" / "results" / "cua_minimax_agent_judge_results.csv",
        "judge": "Kimi K2.6 judge",
        "defines_subset": False,
        "subset_only": True,
    },
]

# Systems reported in the PPTArena paper for which no live result file exists in
# this repo (paper-published subset IF/VQ averages). Parked with the other
# non-Kimi-judged entries for now.
LEADERBOARD_STATIC_ENTRIES_ARCHIVED = [
    {
        "name": "Kimi K2.6",
        "model": "Kimi K2.6",
        "provider": "Moonshot AI",
        "brand": "Kimi",
        "icon": "K",
        "color": "#16181d",
        "split": "subset",
        "expected_cases": 25,
        "judge": "GPT-5.2 judge",
        "if_score": 1.68,
        "vq_score": 1.00,
    },
    {
        "name": "PPTAgent",
        "model": "PPTAgent",
        "provider": "PPTAgent",
        "brand": "PPTAgent",
        "icon": "A",
        "color": "#9ca3af",
        "split": "subset",
        "expected_cases": 25,
        "judge": "GPT-5.2 judge",
        "if_score": 0.00,
        "vq_score": 0.00,
    },
]

LEADERBOARD_STATIC_ENTRIES = []

LEADERBOARD_BASE_SPLITS = [
    {
        "key": "subset",
        "label": "Hard Subset",
        "description": "Matched 25-case hard subset used for cost-sensitive agent comparisons.",
    },
    {
        "key": "full",
        "label": "Full Set",
        "description": "All 100 PPTArena cases, with unscored cases counted as 0.",
    },
]

LEADERBOARD_CATEGORY_SPLITS = [
    {
        "key": "category_content",
        "label": "Content",
        "category": "Content",
        "description": "Cases tagged for content and semantic editing.",
    },
    {
        "key": "category_layout",
        "label": "Layout",
        "category": "Layout",
        "description": "Layout, positioning, and spatial-reasoning cases.",
    },
    {
        "key": "category_styling",
        "label": "Styling",
        "category": "Styling",
        "description": "Visual styling, theme, and typography-sensitive cases.",
    },
    {
        "key": "category_structure",
        "label": "Structure",
        "category": "Structure",
        "description": "Structural, cross-slide, and document-level edits.",
    },
    {
        "key": "category_interactivity",
        "label": "Interactivity",
        "category": "Interactivity",
        "description": "Transitions, animations, actions, and interactive features.",
    },
]

LEADERBOARD_SPLITS = LEADERBOARD_BASE_SPLITS + LEADERBOARD_CATEGORY_SPLITS


def _score_to_float(value):
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _get_case_name(row):
    for key in ("pair_name", "name", "case_name", "Case", "case"):
        value = row.get(key)
        if value:
            return str(value).strip()
    return None


def _read_leaderboard_rows(path):
    """Read rows with IF/VQ judge scores from CSV or JSON result files."""
    if not path.exists():
        return []

    if path.suffix.lower() == ".json":
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return []
        if isinstance(data, dict):
            rows = list(data.values()) if all(isinstance(v, dict) for v in data.values()) else [data]
        elif isinstance(data, list):
            rows = data
        else:
            rows = []
        return [row for row in rows if isinstance(row, dict)]

    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def _load_case_metadata():
    pairs = get_evaluation_pairs()
    by_name = {}
    by_category = {}
    for pair in pairs:
        name = pair.get("name")
        if not name:
            continue
        by_name[name] = pair
        for category in pair.get("category", []):
            by_category.setdefault(category, set()).add(name)
    return by_name, by_category


def _collect_source_scores(source):
    rows = _read_leaderboard_rows(source["path"])
    source_case_names = []
    scored = []
    for row in rows:
        case_name = _get_case_name(row)
        if case_name:
            source_case_names.append(case_name)

        if_score = _score_to_float(row.get("instruction_following_score"))
        vq_score = _score_to_float(row.get("visual_quality_score"))
        if if_score is None or vq_score is None:
            continue
        scored.append({
            "case_name": case_name,
            "if_score": if_score,
            "vq_score": vq_score,
        })

    return scored, source_case_names


def _load_edit_times():
    """Per-system avg edit (generation) times, recorded by
    agent_bench/compute_edit_times.py. Keyed by system id."""
    path = SCRIPT_DIR.parent / "agent_bench" / "results" / "edit_times.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


_EDIT_TIMES = _load_edit_times()


def _edit_time_key(source):
    """System id for a source = its result-CSV basename minus the suffix."""
    p = source.get("path")
    if not p:
        return None
    return Path(p).name.replace("_qwen_judge_results.csv", "").replace("_judge_results.csv", "")


def _fmt_edit_time(seconds):
    if seconds is None:
        return None
    m = seconds / 60.0
    return f"{m:.1f} min" if m >= 1 else f"{seconds:.0f} s"


def _metric_pct(score):
    """Single 0–5 judge metric as a linear percentage of available points."""
    x = max(0.0, min(score, 5.0)) / 5.0
    return x * 100


def _case_pct(if_score, vq_score):
    """Per-case score = equal-weight mean of linear IF and VQ percentages."""
    return (_metric_pct(if_score) + _metric_pct(vq_score)) / 2


def _build_leaderboard_entry(source, split_key, expected_cases, scored):
    scored_cases = len(scored)
    if not scored or expected_cases <= 0:
        return None

    total_if_pct = sum(_metric_pct(row["if_score"]) for row in scored)
    total_vq_pct = sum(_metric_pct(row["vq_score"]) for row in scored)
    total_case_pct = sum(_case_pct(row["if_score"], row["vq_score"]) for row in scored)

    mean_scored_pct = total_case_pct / scored_cases
    # Conservative leaderboard metric: missing or unscored cases count as 0.
    score_pct = total_case_pct / expected_cases
    if_pct = total_if_pct / expected_cases
    vq_pct = total_vq_pct / expected_cases

    return {
        "name": source["name"],
        "model": source.get("model", source["name"]),
        "provider": source.get("provider", ""),
        "brand": source.get("brand", source.get("provider", "")),
        "icon": source.get("icon", source["name"][:1]),
        "color": source.get("color", "#3457d5"),
        "chart_label": source.get("chart_label", source.get("model", source["name"])),
        "split": split_key,
        "score_pct": round(score_pct, 1),
        "bar_pct": round(max(0, min(score_pct, 100)), 1),
        "mean_scored_pct": round(mean_scored_pct, 1),
        "if_pct": round(if_pct, 1),
        "vq_pct": round(vq_pct, 1),
        "scored_cases": scored_cases,
        "expected_cases": expected_cases,
        "judge": source["judge"],
        "coverage_pct": round(scored_cases / expected_cases * 100, 1),
        "edit_time_s": (_EDIT_TIMES.get(_edit_time_key(source)) or {}).get("mean_s"),
        "edit_time_display": _fmt_edit_time((_EDIT_TIMES.get(_edit_time_key(source)) or {}).get("mean_s")),
    }


def _build_static_entry(entry):
    """Build a leaderboard entry from paper-reported IF/VQ averages (no result file)."""
    expected = entry.get("expected_cases", 0)
    if expected <= 0:
        return None
    synthetic = [
        {"case_name": None, "if_score": entry["if_score"], "vq_score": entry["vq_score"]}
        for _ in range(expected)
    ]
    built = _build_leaderboard_entry(entry, entry["split"], expected, synthetic)
    if built:
        built["paper_reported"] = True
    return built


def get_leaderboard_data():
    """Two-level leaderboard: base splits (Hard Subset / Full Set) each with an
    'All' view plus category sub-views (Content, Layout, …) scoped to that base.
    A category with no cases in a base (e.g. Interactivity in the subset) is
    omitted from that base."""
    case_meta, cases_by_category = _load_case_metadata()
    all_case_names = set(case_meta)

    # Canonical 25-case hard subset, pinned to subset25.json so that full-100
    # sources (whose CSVs now cover all 100 cases) can't redefine it.
    try:
        _subset_raw = json.loads((SCRIPT_DIR.parent / "agent_bench" / "subset25.json").read_text(encoding="utf-8"))
        subset_case_names = {n.strip() for n in _subset_raw} & all_case_names
    except Exception:
        subset_case_names = set()

    source_payloads = []
    for source in LEADERBOARD_SOURCES:
        scored, source_case_names = _collect_source_scores(source)
        source_payloads.append((source, scored, source_case_names))
    if not subset_case_names:  # fallback: derive from defines_subset sources
        for source, scored, scn in source_payloads:
            if source["split"] == "subset" and source.get("defines_subset", True):
                subset_case_names.update(scn)

    base_cases = {"subset": subset_case_names, "full": all_case_names}
    base_labels = {"subset": "Hard Subset", "full": "Full Set"}
    base_desc = {
        "subset": "Matched 25-case hard subset used for cost-sensitive agent comparisons.",
        "full": "All 100 PPTArena cases, with unscored cases counted as 0.",
    }

    def build_view(view_key, view_cases, base_key):
        entries = []
        for source, scored, source_case_names in source_payloads:
            # Subset-only systems (e.g. CUA product agents, run on the 25-case
            # subset) don't appear on the Full Set — they'd read as 25/100.
            if base_key == "full" and source.get("subset_only"):
                continue
            # Every system is scored against the canonical cases in this view.
            # A missing row therefore remains in the denominator and counts as 0.
            expected = view_cases
            if not expected:
                continue
            view_scores = [r for r in scored if r["case_name"] in expected]
            e = _build_leaderboard_entry(source, view_key, len(expected), view_scores)
            if e:
                entries.append(e)
        for st in LEADERBOARD_STATIC_ENTRIES:
            if st.get("split") == base_key and (view_key == base_key):
                b = _build_static_entry(st)
                if b:
                    entries.append(b)
        entries.sort(key=lambda r: r["score_pct"], reverse=True)
        return entries

    groups = []
    panel_list = []
    for base_key in ("subset", "full"):
        bcases = base_cases[base_key]
        n_all = len(all_case_names) if base_key == "full" else (len(bcases) or 25)
        views = []
        # "All" view = the base split itself
        all_entries = build_view(base_key, bcases if bcases else all_case_names, base_key)
        v_all = {"key": base_key, "label": "All", "description": base_desc[base_key],
                 "case_count": n_all, "system_count": len(all_entries), "entries": all_entries}
        views.append(v_all)
        panel_list.append(v_all)
        # category sub-views scoped to this base
        for cat in LEADERBOARD_CATEGORY_SPLITS:
            cat_cases = bcases & cases_by_category.get(cat["category"], set())
            if not cat_cases:
                continue
            key = f"{base_key}:{cat['key']}"
            ent = build_view(key, cat_cases, base_key)
            v = {"key": key, "label": cat["label"], "description": cat["description"],
                 "case_count": len(cat_cases), "system_count": len(ent), "entries": ent}
            views.append(v)
            panel_list.append(v)
        groups.append({"base": base_key, "label": base_labels[base_key], "views": views})

    return {
        "default_split": "full",
        "groups": groups,
        "panel_list": panel_list,
    }


def build_evaluation_context(selected_pair_name=None, prediction_pptx_path=None):
    """Build the evaluation page context."""
    evaluation_pairs = get_evaluation_pairs()
    selected_pair = next(
        (p for p in evaluation_pairs if p['name'] == selected_pair_name),
        evaluation_pairs[0] if evaluation_pairs else None
    )

    if not selected_pair:
        return None, "Error: evaluation_pairs_refined.json is missing or empty."

    gt_ppt_path = SCRIPT_DIR.parent / selected_pair['ground_truth']
    gt_pdf = convert_pptx_to_pdf(str(gt_ppt_path), app.config['GENERATED_PDFS_FOLDER'])

    pred_pdf = None
    pred_ppt = None
    is_prediction = False
    if prediction_pptx_path and Path(prediction_pptx_path).exists():
        pred_ppt = Path(prediction_pptx_path)
        pred_pdf = convert_pptx_to_pdf(str(pred_ppt), app.config['GENERATED_PDFS_FOLDER'])
        is_prediction = True

    if not pred_pdf and not is_prediction:
        initial_pred_ppt = SCRIPT_DIR.parent / selected_pair['original']
        if initial_pred_ppt.exists():
            pred_ppt = initial_pred_ppt
            pred_pdf = convert_pptx_to_pdf(str(pred_ppt), app.config['GENERATED_PDFS_FOLDER'])

    prediction_pptx_name = pred_ppt.name if pred_ppt else None

    gt_pdf_name = Path(gt_pdf).name if gt_pdf else None
    pred_pdf_name = Path(pred_pdf).name if pred_pdf else None

    # Generate public URLs for MS Viewer fallback
    # Note: We use _external=True to get absolute URLs. 
    # ProxyFix ensures these are https:// if the request came via HTTPS.
    # For static files in the parent dir, we use the 'serve_file_in_root' endpoint.
    public_gt_url = url_for('serve_file_in_root', filepath=selected_pair['ground_truth'], _external=True)
    
    public_pred_url = None
    
    # For the "Original" or "Prediction" slot:
    if is_prediction:
         # It's a bit complex to reconstruct the URL for an arbitrary path without session context here.
         # But if we are in 'app_page' or 'evaluation_page', we might not have session_id easily if it's just a path.
         # However, if prediction_pptx_path is passed, it's an absolute path.
         # If it is in MODIFIED_PPTX_FOLDER, we can serve it.
         if pred_ppt and str(app.config['MODIFIED_PPTX_FOLDER']) in str(pred_ppt):
             public_pred_url = url_for('public_modified', filename=pred_ppt.name, _external=True)
             pass
    else:
         # It is the original file from the pair
         public_pred_url = url_for('serve_file_in_root', filepath=selected_pair['original'], _external=True)

    context = dict(
        evaluation_pairs=evaluation_pairs,
        selected_pair=selected_pair,
        test_pdf_name=gt_pdf_name,
        prediction_pdf_name=pred_pdf_name,
        prediction_pptx_name=prediction_pptx_name,
        is_prediction=is_prediction,
        public_gt_url=public_gt_url,
        public_pred_url=public_pred_url,
        leaderboard_data=get_leaderboard_data()
    )
    return context, None

@app.route('/app')
def app_page():
    """Legacy entry point; the site is evaluation-only now."""
    args = request.args.to_dict(flat=True)
    args.pop('tab', None)
    return redirect(url_for('evaluation_page', **args))


def get_evaluation_pairs():
    """Reads and returns the evaluation pairs from the JSON file."""
    try:
        with open(SCRIPT_DIR / 'evaluation_pairs_refined.json', 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

@app.route('/evaluation', methods=['GET'])
def evaluation_page():
    """Render the evaluation workspace."""
    selected_pair_name = request.args.get('pair')
    prediction_path = request.args.get('prediction_pptx_path')
    context, error = build_evaluation_context(selected_pair_name, prediction_path)
    if error:
        return error, 500
    return render_template('evaluation.html', active_tab='evaluation', **context)


@app.route('/evaluation/google944d95ae4c99977e.html', methods=['GET'])
def google_site_verification():
    return send_from_directory(SCRIPT_DIR, 'google944d95ae4c99977e.html')

@app.route('/download_original/<session_id>/<filename>')
def download_original_file(session_id, filename):
    session_path = Path(app.config['SESSIONS_FOLDER']) / session_id
    return send_from_directory(session_path, filename, as_attachment=True)

@app.route('/download_modified/<session_id>/<filename>')
def download_modified_file(session_id, filename):
    session_path = Path(app.config['SESSIONS_FOLDER']) / session_id
    return send_from_directory(session_path, filename, as_attachment=True)

@app.route('/preview_ppt/original/<session_id>/<filename>')
def preview_original_ppt(session_id, filename):
    session_path = Path(app.config['SESSIONS_FOLDER']) / session_id
    return send_from_directory(session_path, filename, as_attachment=False)

@app.route('/preview_ppt/original/<filename>')
def preview_original_upload(filename):
    """Serve a temporarily uploaded PPTX before a session is created."""
    return send_from_directory(app.config['USER_UPLOADS_FOLDER'], filename, as_attachment=False)

@app.route('/preview_ppt/modified/<session_id>/<filename>')
def preview_modified_ppt(session_id, filename):
    session_path = Path(app.config['SESSIONS_FOLDER']) / session_id
    return send_from_directory(session_path, filename, as_attachment=False)

@app.route('/view_slide_image/<path:image_path>')
def view_slide_image(image_path):
    """Serves an image from the generated_images directory."""
    return send_from_directory(app.config['GENERATED_IMAGES_FOLDER'], image_path, as_attachment=False)

@app.route('/view_pdf/<path:pdf_path>')
def view_pdf(pdf_path):
    """Serve a PDF from the generated_pdfs directory."""
    return send_from_directory(app.config['GENERATED_PDFS_FOLDER'], pdf_path, as_attachment=False)

@app.route('/public/preview/<path:filename>')
def public_preview(filename):
    """Serve PPTX files publicly for Microsoft Live viewer without requiring cookies."""
    # For now, serve from user uploads - in production, this should be a signed URL to S3/GCS
    try:
        response = send_from_directory(
            app.config['USER_UPLOADS_FOLDER'], 
            filename, 
            as_attachment=False,
            mimetype='application/vnd.openxmlformats-officedocument.presentationml.presentation'
        )
        # Set proper headers for Microsoft Live viewer
        response.headers['Content-Disposition'] = f'inline; filename="{filename}"'
        response.headers['Cache-Control'] = 'private, max-age=0'
        return response
    except FileNotFoundError:
        return "File not found", 404


@app.route('/files/<path:filepath>')
def serve_file_in_root(filepath):
    """Serve files from the root directory for evaluation purposes."""
    return send_from_directory(SCRIPT_DIR.parent, filepath)

@app.route('/process_eval_prediction', methods=['POST'])
def process_eval_prediction():
    """Process a PPTX on the evaluation page to produce a prediction."""
    import orchestrator
    generation_start_time = time.time()
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file part in request.'}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected.'}), 400

        if not allowed_file(file.filename):
            return jsonify({'error': 'File type not allowed.'}), 400

        prompt_text = request.form.get('prompt', '')
        selected_model_id = request.form.get('llm_engine')
        use_pre_analysis = request.form.get('use_pre_analysis', 'on') == 'on'
        api_keys = {
            "openai": request.form.get('openai_api_key'),
            "gemini": request.form.get('gemini_api_key')
        }
        force_python_pptx = request.form.get('force_python_pptx') == 'on'
        
        # --- API Key Validation ---
        is_valid, error_msg = check_api_key(selected_model_id, api_keys)
        if not is_valid:
            return jsonify({'error': error_msg}), 400
            
        loop_mode = request.form.get('loop_mode') == 'on'
        loop_iterations_raw = request.form.get('loop_iterations')
        try:
            loop_iterations = int(loop_iterations_raw) if loop_iterations_raw else 1
        except ValueError:
            loop_iterations = 1
        loop_iterations = max(1, loop_iterations)

        original_filename_secure = secure_filename(file.filename)
        temp_filename = f"eval_{uuid.uuid4().hex}_{original_filename_secure}"
        original_filepath = USER_UPLOADS_FOLDER / temp_filename
        file.save(original_filepath)

        # Prefer client-provided request id for continuity if present
        client_request_id = request.form.get('client_request_id')
        request_id = client_request_id or f"eval-{uuid.uuid4().hex}"
        progress.start(request_id)
        progress.append(request_id, "Started processing evaluation request")

        def background_processing():
            processing_result = orchestrator.process_presentation_hybrid(
                original_filepath=str(original_filepath),
                prompt_text=prompt_text,
                selected_model_id=selected_model_id,
                use_pre_analysis=use_pre_analysis,
                request_id=request_id,
                api_keys=api_keys,
                force_python_pptx=force_python_pptx,
                loop_mode=loop_mode,
                loop_max_iterations=loop_iterations,
            )

            planning_plan = processing_result.get('planning_plan') if isinstance(processing_result, dict) else None
            planning_model = processing_result.get('planning_model') if isinstance(processing_result, dict) else None
            if isinstance(planning_plan, dict):
                target_count = len(planning_plan.get('targets') or [])
                progress.append(
                    request_id,
                    f"Planning via {planning_model or 'gpt-5-nano'} selected {target_count} target file(s)."
                )

            if processing_result.get('error'):
                generation_time = round(time.time() - generation_start_time, 2)
                processing_result['generation_time_seconds'] = generation_time
                return processing_result

            modified_pptx_path = processing_result.get('modified_pptx_filepath')
            if not modified_pptx_path:
                generation_time = round(time.time() - generation_start_time, 2)
                reason = processing_result.get('reason_for_no_modification') or "No modified PPTX was produced by the pipeline."
                progress.append(request_id, "No modified PPTX produced; skipping PDF conversion")
                return {
                    "error": reason,
                    "request_id": request_id,
                    "generation_time_seconds": generation_time,
                }

            pred_pdf = convert_pptx_to_pdf(modified_pptx_path, GENERATED_PDFS_FOLDER)
            progress.append(request_id, "Converted prediction to PDF")

            generation_time = round(time.time() - generation_start_time, 2)
            progress.append(request_id, f"Finished processing (took {generation_time}s)")
            # Always generate the public preview URL for the MS Viewer fallback
            # The file is in MODIFIED_PPTX_FOLDER, so we use the 'public_modified' route
            public_preview_url = url_for('public_modified', filename=Path(modified_pptx_path).name, _external=True)

            return {
                'prediction_pdf_name': Path(pred_pdf).name if pred_pdf else None,
                'prediction_pptx_name': Path(modified_pptx_path).name if modified_pptx_path else None,
                'modified_pptx_filepath': str(modified_pptx_path) if modified_pptx_path else None,
                'public_preview_url': public_preview_url,
                'message': 'Processing successful!' if pred_pdf else 'Processing successful (PDF preview unavailable, using MS Viewer).',
                'request_id': request_id,
                'generation_time_seconds': generation_time,
                'loop_mode_enabled': processing_result.get('loop_mode_enabled', False),
                'loop_iterations_requested': processing_result.get('loop_iterations_requested'),
                'loop_iterations_completed': processing_result.get('loop_iterations_completed'),
                'loop_iteration_summaries': processing_result.get('loop_iteration_summaries'),
                'planning_plan': processing_result.get('planning_plan'),
                'planning_model': processing_result.get('planning_model'),
            }

        from flask import current_app
        return stream_long_task(current_app._get_current_object().app_context(), background_processing)
    except AttributeError as e:
        # Explicitly catch the circular import error and return it as JSON
        error_message = {
            "error": "Circular Import Error Detected on Server",
            "details": f"The server crashed with an 'AttributeError: {e}'. This is a classic symptom of a circular import loop (e.g., app.py -> orchestrator.py -> some_other_module.py -> app.py).",
            "solution": "To fix this, the 'import orchestrator' statement must be moved inside the function that uses it, instead of being at the top of the file. Please accept the next change to apply the permanent fix."
        }
        app.logger.error(f"Circular Import Suspected: {e}", exc_info=True)
        return jsonify(error_message), 500
    except Exception as e:
        app.logger.error(f"An unexpected error occurred in process_eval_prediction: {e}", exc_info=True)
        return jsonify({"error": f"An unexpected server error occurred: {str(e)}"}), 500


@app.route('/public/modified/<path:filename>')
def public_modified(filename):
    """Serves modified PPTX files for public preview (e.g. MS Viewer)."""
    return send_from_directory(app.config['MODIFIED_PPTX_FOLDER'], filename)

@app.route('/upload_only', methods=['POST'])
def upload_only_route():
    """Handles file upload for quick preview without processing."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file part in request.'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if file and allowed_file(file.filename):
        original_filename_secure = secure_filename(file.filename)
        unique_id = uuid.uuid4().hex[:8]
        save_filename = f"{unique_id}_{original_filename_secure}"
        saved_filepath = os.path.join(app.config['USER_UPLOADS_FOLDER'], save_filename)
        file.save(saved_filepath)
        preview_url = f"/preview_ppt/original/{save_filename}"
        # Generate public preview URL for Microsoft Live viewer
        public_preview_url = url_for('public_preview', filename=save_filename, _external=True)
        pdf_preview_url = generate_pdf_preview_url(saved_filepath)
        return jsonify({
            'preview_url': preview_url,
            'public_preview_url': public_preview_url,
            'pdf_preview_url': pdf_preview_url
        }), 200
    else:
        return jsonify({'error': 'File type not allowed'}), 400

@app.route('/judge', methods=['POST'])
def judge_edit_route():
    """
    On-demand endpoint to call the LLM judge for a specific slide edit.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON payload"}), 400

        user_prompt = data.get('instruction') # Keep 'instruction' from frontend for now
        original_slide_image_b64 = data.get('original_slide_image_b64')
        modified_slide_image_b64 = data.get('modified_slide_image_b64')
        original_slide_xml = data.get('original_slide_xml')
        modified_slide_xml = data.get('modified_slide_xml')
        judge_model = data.get('model_id', 'gemini-3-pro-preview') # Default to a powerful model
        api_keys = {
            "openai": data.get('openai_api_key'),
            "gemini": data.get('gemini_api_key')
        }
        request_id = data.get('request_id', 'judging')

        # --- API Key Validation ---
        is_valid, error_msg = check_api_key(judge_model, api_keys)
        if not is_valid:
            return jsonify({"error": error_msg}), 400

        # Basic validation
        if not all([user_prompt, original_slide_image_b64, modified_slide_image_b64, original_slide_xml, modified_slide_xml]):
            return jsonify({"error": "Missing required fields for judging"}), 400
        
        # --- Call Judge ---
        judge_result = llm_handler.call_llm_judge(
            user_prompt=user_prompt,
            original_slide_image_b64=original_slide_image_b64,
            modified_slide_image_b64=modified_slide_image_b64,
            original_slide_xml=original_slide_xml,
            modified_slide_xml=modified_slide_xml,
            judge_model=judge_model,
            request_id=request_id,
            api_keys=api_keys
        )

        return jsonify(judge_result)

    except Exception as e:
        app.logger.error(f"Error during judging: {e}", exc_info=True)
        return jsonify({"error": f"An error occurred during judging: {str(e)}"}), 500

@app.route('/process_ppt', methods=['POST'])
def process_ppt_route():
    """
    Main endpoint for processing PPTX files with user prompts.
    Creates a new session and processes the presentation.
    """
    import orchestrator
    start_time = time.time()
    data = request.form
    
    # Validate required fields
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'File type not allowed. Only .pptx files are supported.'}), 400
    
    prompt_text = data.get('prompt', '')
    selected_model_id = data.get('llm_engine')
    use_pre_analysis = data.get('use_pre_analysis', 'on') == 'on'
    api_keys = {
        "openai": data.get('openai_api_key'),
        "gemini": data.get('gemini_api_key')
    }
    force_python_pptx = data.get('force_python_pptx') == 'on'
    
    # --- API Key Validation ---
    is_valid, error_msg = check_api_key(selected_model_id, api_keys)
    if not is_valid:
        return jsonify({'error': error_msg}), 400
    
    if not all([prompt_text, selected_model_id]):
        return jsonify({'error': 'Missing required fields: prompt and llm_engine'}), 400
    
    # Create session directory
    session_id = f"session_{uuid.uuid4().hex[:8]}"
    session_path = Path(app.config['SESSIONS_FOLDER']) / session_id
    session_path.mkdir(parents=True, exist_ok=True)
    
    # Save uploaded file
    original_filename_secure = secure_filename(file.filename)
    original_filepath = session_path / 'original.pptx'
    file.save(original_filepath)
    
    request_id = f"{session_id}-{int(time.time())}"
    progress.start(request_id)
    progress.append(request_id, "Started processing presentation")
    
    def background_processing():
        # Process the presentation
        processing_result = orchestrator.process_presentation_hybrid(
            original_filepath=str(original_filepath),
            prompt_text=prompt_text,
            selected_model_id=selected_model_id,
            use_pre_analysis=use_pre_analysis,
            request_id=request_id,
            api_keys=api_keys,
            session_id=session_id,
            force_python_pptx=force_python_pptx
        )
        
        if processing_result.get("error"):
            return processing_result
        
        # Set up URLs for the response
        processing_result["session_id"] = session_id
        processing_result["original_pptx_download_url"] = f"/download_original/{session_id}/original.pptx"
        processing_result["original_pptx_url"] = f"/preview_ppt/original/{session_id}/original.pptx"
        
        if processing_result.get("modified_pptx_filepath"):
            modified_path = Path(processing_result["modified_pptx_filepath"])
            if modified_path.exists():
                # Copy to session directory only if it's a different file
                session_modified_path = session_path / 'modified.pptx'
                try:
                    if modified_path.resolve() != session_modified_path.resolve():
                        shutil.copy(modified_path, session_modified_path)
                except shutil.SameFileError:
                    # File is already in the right place, no need to copy
                    pass
                
                processing_result["modified_pptx_download_url"] = f"/download_modified/{session_id}/modified.pptx"
                processing_result["modified_pptx_url"] = f"/preview_ppt/modified/{session_id}/modified.pptx"
                # Generate public preview URL for Microsoft Live viewer
                # Use preview_modified_ppt route which serves from session folder
                processing_result["public_preview_url"] = url_for('preview_modified_ppt', session_id=session_id, filename='modified.pptx', _external=True)
                pdf_url = generate_pdf_preview_url(session_modified_path)
                if pdf_url:
                    processing_result["pdf_preview_url"] = pdf_url
        
        progress.append(request_id, "Finished processing")
        processing_result["request_id"] = request_id
        return processing_result

    from flask import current_app
    return stream_long_task(current_app._get_current_object().app_context(), background_processing)

@app.route('/save_evaluation_result', methods=['POST'])
def save_evaluation_result():
    """Save evaluation results to CSV file."""
    try:
        data = request.get_json(silent=True) or {}
        
        # Extract data from request
        pair_name = data.get('pair_name', '')
        llm_engine = data.get('llm_engine', '')
        generation_time = data.get('generation_time_seconds', '')
        judge_time = data.get('judge_time_seconds', '')
        instruction_following_score = data.get('instruction_following_score', '')
        visual_quality_score = data.get('visual_quality_score', '')
        instruction_following_reason = data.get('instruction_following_reason', '')
        visual_quality_reason = data.get('visual_quality_reason', '')
        judge_model = data.get('judge_model', '')
        
        # Check if CSV exists to determine if we need to write header
        file_exists = EVALUATION_RESULTS_CSV.exists()
        
        # Prepare row data
        row = {
            'timestamp': datetime.now().isoformat(),
            'pair_name': pair_name,
            'llm_engine': llm_engine,
            'generation_time_seconds': generation_time,
            'judge_model': judge_model,
            'judge_time_seconds': judge_time,
            'instruction_following_score': instruction_following_score,
            'visual_quality_score': visual_quality_score,
            'instruction_following_reason': instruction_following_reason,
            'visual_quality_reason': visual_quality_reason
        }
        
        # Append to CSV
        with open(EVALUATION_RESULTS_CSV, 'a', newline='', encoding='utf-8') as csvfile:
            fieldnames = [
                'timestamp', 'pair_name', 'llm_engine', 'generation_time_seconds',
                'judge_model', 'judge_time_seconds', 'instruction_following_score',
                'visual_quality_score', 'instruction_following_reason', 
                'visual_quality_reason'
            ]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            if not file_exists:
                writer.writeheader()
            
            writer.writerow(row)
        
        return jsonify({'success': True, 'message': 'Results saved to CSV'})
    except Exception as e:
        app.logger.error(f"Error saving evaluation result: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@app.route('/judge_arena', methods=['POST'])
def judge_arena_route():
    """Judge ground truth vs prediction using arena prompt."""
    judge_start_time = time.time()
    try:
        data = request.get_json(silent=True) or {}
        selected_pair_name = data.get('pair_name')
        prediction_name = data.get('prediction_pptx_name')
        judge_model = data.get('judge_model') or 'gemini-3-pro-preview'

        api_keys = {
            "openai": data.get('openai_api_key'),
            "gemini": data.get('gemini_api_key')
        }
        
        # --- API Key Validation ---
        is_valid, error_msg = check_api_key(judge_model, api_keys)
        if not is_valid:
            return jsonify({'error': error_msg}), 400

        evaluation_pairs = get_evaluation_pairs()
        selected_pair = next((p for p in evaluation_pairs if p['name'] == selected_pair_name), None)

        if not selected_pair:
            return jsonify({'error': f"Evaluation pair '{selected_pair_name}' not found."}), 404

        # --- Define the three PPTX paths ---
        initial_ppt_path = SCRIPT_DIR.parent / selected_pair['original']
        gt_ppt_path = SCRIPT_DIR.parent / selected_pair['ground_truth']
        pred_ppt = None
        
        if prediction_name:
            # Check for the prediction file in all relevant folders
            possible_paths = [
                MODIFIED_PPTX_FOLDER / prediction_name,
                SCRIPT_DIR.parent / prediction_name,
                USER_UPLOADS_FOLDER / prediction_name
            ]
            for path in possible_paths:
                if path.exists():
                    pred_ppt = path
                    break
            if not pred_ppt:
                 return jsonify({'error': f"Prediction file '{prediction_name}' not found."}), 404
        else:
            # Fallback to the initial file if no prediction has been made
            pred_ppt = initial_ppt_path

        if not pred_ppt or not pred_ppt.exists():
             return jsonify({'error': 'A valid prediction presentation file could not be found.'}), 404

        # --- Generate full JSON summaries for all three presentations ---
        initial_json = pptx_to_json(str(initial_ppt_path))
        gt_json = pptx_to_json(str(gt_ppt_path))
        pred_json = pptx_to_json(str(pred_ppt))

        with tempfile.TemporaryDirectory() as tmpdir:
            init_dir = Path(tmpdir) / "initial"
            gt_dir = Path(tmpdir) / "ground_truth"
            pred_dir = Path(tmpdir) / "prediction"
            init_imgs = export_slides_to_images(str(initial_ppt_path), str(init_dir))
            gt_imgs = export_slides_to_images(str(gt_ppt_path), str(gt_dir))
            pred_imgs = export_slides_to_images(str(pred_ppt), str(pred_dir))
            init_b64_all = [image_to_base64(p) for p in init_imgs]
            gt_b64_all = [image_to_base64(p) for p in gt_imgs]
            pred_b64_all = [image_to_base64(p) for p in pred_imgs]

        gt_xml = extract_specific_xml_from_pptx(str(gt_ppt_path), 'ppt/slides/slide1.xml') or ''
        pred_xml = extract_specific_xml_from_pptx(str(pred_ppt), 'ppt/slides/slide1.xml') or ''

        judge_result = llm_handler.call_llm_judge(
            user_prompt=f"Instruction: {selected_pair['prompt']}\nStyle Target: {selected_pair['style_target']}",
            initial_ppt_json=initial_json,
            original_ppt_json=gt_json,
            modified_ppt_json=pred_json,
            initial_slide_images_b64=init_b64_all,
            original_slide_images_b64=gt_b64_all,
            modified_slide_images_b64=pred_b64_all,
            original_slide_xml=gt_xml,
            modified_slide_xml=pred_xml,
            judge_model=judge_model,
            evaluation_mode='arena',
            api_keys={
                "openai": data.get('openai_api_key'),
                "gemini": data.get('gemini_api_key')
            }
        )

        if not judge_result:
             return jsonify({'error': 'Judge returned no result.'}), 500
             
        if judge_result.get('error'):
             return jsonify(judge_result), 500

        # Ensure frontend receives expected keys
        judge_result.setdefault('instruction_following_score', 'N/A')
        judge_result.setdefault('visual_quality_score', 'N/A')
        judge_result.setdefault('instruction_following_reason', '')
        judge_result.setdefault('visual_quality_reason', '')
        
        judge_time = round(time.time() - judge_start_time, 2)
        judge_result['judge_time_seconds'] = judge_time
        
        return jsonify(judge_result)
    except Exception as e:
        app.logger.error(f"Error during arena judging: {e}", exc_info=True)
        judge_time = round(time.time() - judge_start_time, 2)
        return jsonify({'error': str(e), 'judge_time_seconds': judge_time}), 500



@app.route('/api/edit', methods=['POST'])
def edit_existing_ppt_route():
    """
    Stateful endpoint for continued editing of a presentation.
    """
    import orchestrator
    start_time = time.time()
    data = request.form
    session_id = data.get('session_id')
    prompt_text = data.get('prompt')
    selected_model_id = data.get('llm_engine')
    use_pre_analysis = data.get('use_pre_analysis', 'on') == 'on'
    api_keys = {
        "openai": data.get('openai_api_key'),
        "gemini": data.get('gemini_api_key')
    }
    force_python_pptx = data.get('force_python_pptx') == 'on'
    
    # --- API Key Validation ---
    is_valid, error_msg = check_api_key(selected_model_id, api_keys)
    if not is_valid:
        return jsonify({'error': error_msg}), 400
    
    if not all([session_id, prompt_text, selected_model_id]):
        return jsonify({'error': 'Missing session_id, prompt, or llm_engine'}), 400

    session_path = Path(app.config['SESSIONS_FOLDER']) / session_id
    if not session_path.exists():
        return jsonify({'error': 'Session not found'}), 404

    # The 'current' version to be edited is the last modified one.
    current_ppt_path = session_path / 'modified.pptx'
    if not current_ppt_path.exists():
        return jsonify({'error': 'No modifiable presentation found in session'}), 404
        
    request_id = f"{session_id}-{int(time.time())}"
    progress.start(request_id)
    progress.append(request_id, "Started editing session presentation")
    
    def background_processing():
        # Process the presentation. This function is now the core logic.
        processing_result = orchestrator.process_presentation_hybrid(
            original_filepath=str(current_ppt_path),
            prompt_text=prompt_text,
            selected_model_id=selected_model_id,
            use_pre_analysis=use_pre_analysis,
            request_id=request_id,
            api_keys=api_keys,
            session_id=session_id, # Pass session_id to logic
            force_python_pptx=force_python_pptx
        )

        if processing_result.get("error"):
            return processing_result

        # Overwrite the 'modified.pptx' with the new version while keeping history
        if processing_result.get("modified_pptx_filepath"):
            newly_modified_path = Path(processing_result["modified_pptx_filepath"])
            if newly_modified_path.exists():
                # Save the previous version before overwriting
                history_files = sorted(session_path.glob("history_*.pptx"))
                next_idx = len(history_files) + 1
                history_path = session_path / f"history_{next_idx}.pptx"
                if current_ppt_path.exists() and current_ppt_path.resolve() != history_path.resolve():
                    shutil.copy(current_ppt_path, history_path)

                # Replace the current modifiable file if different
                if newly_modified_path.resolve() != current_ppt_path.resolve():
                    shutil.copy(newly_modified_path, current_ppt_path)


                # Update URLs for frontend
                processing_result["modified_pptx_download_url"] = f"/download_modified/{session_id}/modified.pptx"
                processing_result["modified_pptx_url"] = f"/preview_ppt/modified/{session_id}/modified.pptx"
                processing_result["original_pptx_download_url"] = f"/download_original/{session_id}/{history_path.name}"
                processing_result["original_pptx_url"] = f"/preview_ppt/original/{session_id}/{history_path.name}"
                # Generate public preview URL for Microsoft Live viewer
                # History files are in session folder, served by preview_original_ppt? No, download_original serves from session.
                # We need a route to serve history files publicly.
                # preview_original_ppt serves from session folder.
                processing_result["public_preview_url"] = url_for('preview_original_ppt', session_id=session_id, filename=history_path.name, _external=True)
                pdf_url = generate_pdf_preview_url(current_ppt_path)
                if pdf_url:
                    processing_result["pdf_preview_url"] = pdf_url


        progress.append(request_id, "Finished processing")
        processing_result["request_id"] = request_id
        return processing_result

    from flask import current_app
    return stream_long_task(current_app._get_current_object().app_context(), background_processing)

@app.route('/progress', methods=['GET'])
def get_progress():
    request_id = request.args.get('request_id', '')
    since = int(request.args.get('since', '0') or '0')
    messages = progress.get(request_id, since)
    return jsonify({
        'request_id': request_id,
        'since': since,
        'messages': messages,
        'next_index': since + len(messages)
    })


if __name__ == '__main__':
    # Note: The benchmark runner expects the host to be 127.0.0.1 and port 5001
    # Render provides the PORT environment variable
    port = int(os.environ.get("PORT", 5001))
    app.run(host='0.0.0.0', port=port, debug=True)
