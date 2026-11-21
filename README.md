# PPTArena: A Benchmark for Agentic PowerPoint Editing

<div align="center">

[![arXiv](https://img.shields.io/badge/arXiv-2512.03042-b31b1b.svg)](https://arxiv.org/abs/2512.03042)
[![Hugging Face Dataset](https://img.shields.io/badge/%F0%9F%A4%97%20Hugging%20Face-Dataset-ffd21e.svg)](https://huggingface.co/datasets/mofengenden/PPTArena)
[![Live Leaderboard](https://img.shields.io/badge/%F0%9F%8F%86%20Live-Leaderboard-2563eb.svg)](https://ppt-arena.onrender.com/evaluation)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**[Paper](https://arxiv.org/abs/2512.03042) · [Dataset on Hugging Face](https://huggingface.co/datasets/mofengenden/PPTArena) · [Live Leaderboard & WebApp](https://ppt-arena.onrender.com/evaluation)**

</div>

## Abstract

We introduce **PPTArena**, a benchmark for PowerPoint editing that measures reliable modifications to real slides under natural-language instructions. In contrast to image-PDF renderings or text-to-slide generation, PPTArena focuses on in-place editing across 100 decks, 2125 slides, and over 800 targeted edits covering text, charts, tables, animations, and master-level styles. Each case includes a ground-truth deck, a fully specified target outcome, and a dual VLM-as-judge pipeline that separately scores instruction following and visual quality using both structural diffs and slide images. 

Building on this setting, we propose **PPTPilot**, a structure-aware slide-editing agent that plans semantic edit sequences, routes between high-level programmatic tools and deterministic XML operations for precise control, and verifies outputs through an iterative plan-edit-check loop against task-specific constraints. In our experiments, PPTPilot outperforms strong proprietary agents and frontier VLM systems by over 10 percentage points on compound, layout-sensitive, and cross-slide edits, with particularly large gains in visual fidelity and deck-wide consistency.

The benchmark dataset is available on [Hugging Face](https://huggingface.co/datasets/mofengenden/PPTArena).

## Features

- **Agentic Editing**: Edit PowerPoint presentations using natural language instructions.
- **Dual-View Evaluation**: Compare "Original" vs "Ground Truth" slides side-by-side.
- **Iterative Refinement**: The agent plans, executes, and verifies edits in a loop.
- **Multi-Modal Judge**: Automated evaluation using VLM-as-a-judge for both instruction following and visual quality.
- **Comprehensive Benchmark**: Covers diverse tasks including text editing, chart manipulation, layout adjustments, and image handling.

**Live leaderboard:** [ppt-arena.onrender.com/evaluation](https://ppt-arena.onrender.com/evaluation) · **Live evaluation webapp:** [ppt-arena.onrender.com/workspace](https://ppt-arena.onrender.com/workspace)

<div align="center">
<img src="src/pptarenawebapp.png" alt="PPTArena Teaser" width="100%">
</div>



## Directory Structure

- `src/`: Core application code (Flask app, LLM handlers, PPT processing) and configuration files (`requirements.txt`, `evaluation_pairs_refined.json`).
- `Original/`: Benchmark dataset - Original PowerPoint files.
- `GroundTruth/`: Benchmark dataset - Ground Truth PowerPoint files.
- `run_benchmark.py` / `run_evaluation.py`: Scripts to run the benchmark and score predictions with the LLM judge.

## Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/michaelofengenden/PPTArena.git
    cd PPTArena
    ```

2.  **Install dependencies:**
    ```bash
    pip install -r src/requirements.txt
    ```

3.  **Configure API Keys:**
    Create a `credentials.env` file in the root directory:
    ```env
    OPENAI_API_KEY=your_openai_key
    GEMINI_API_KEY=your_gemini_key
    ```

## Usage

1.  **Start the Application:**
    ```bash
    python src/app.py
    ```

2.  **Access the Web Interface:**
    Open `http://localhost:5001` in your browser.

3.  **Evaluate:**
    - Browse the **leaderboard** and pick a benchmark split (hard subset, full set, or per-category).
    - Select a test case to see the Original and Ground Truth side by side.
    - Click "Generate prediction" to run the agent on the task (or upload your own prediction `.pptx`).
    - Use "Call LLM judge" to score the result on instruction following and visual quality.

## Citation

If you find this work useful, please cite our paper:

```bibtex
@article{ofengenden2025pptarena,
  title={PPTArena: A Benchmark for Agentic PowerPoint Editing},
  author={Ofengenden, Michael and Man, Yunze and Pang, Ziqi and Gui, Liang-Yan and Wang, Yu-Xiong},
  journal={arXiv preprint arXiv:2512.03042},
  year={2025}
}
```
