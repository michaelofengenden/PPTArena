import json
import os
import re
import time
import base64
import io
import csv
from datetime import datetime
from typing import Optional, List
import openai
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold
from pathlib import Path
from PIL import Image
import numpy as np

from llm.utils import (
    load_api_keys,
    _create_openai_client,
    _create_kimi_client,
    _create_openrouter_client,
    _configure_gemini_client,
    _build_gemini_generation_config,
    extract_json_from_llm_response,
    _extract_text_from_openai_response,
    _is_kimi_model,
    _is_openai_model,
    _is_openrouter_model,
    _log
)
from utils.image_utils import (
    _pil_from_b64,
    _resize_for_metric,
    _to_gray_np,
    _compute_ssim,
    _dct_8x8_hash,
    _hamming_distance64
)
import ppt as ppt_processor

def call_llm_judge(
    user_prompt: str,
    judge_model: str,
    original_slide_xml: str,
    modified_slide_xml: str,
    original_slide_image_b64: Optional[str] = None,
    initial_slide_image_b64: Optional[str] = None,
    modified_slide_image_b64: Optional[str] = None,
    initial_slide_images_b64: Optional[List[str]] = None,
    original_slide_images_b64: Optional[List[str]] = None,
    modified_slide_images_b64: Optional[List[str]] = None,
    initial_ppt_json: Optional[dict] = None,
    original_ppt_json: Optional[dict] = None,
    modified_ppt_json: Optional[dict] = None,
    request_id: Optional[str] = None,
    api_key: Optional[str] = None,
    api_keys: Optional[dict] = None,
    evaluation_mode: str = "standard",
):
    """
    Calls the LLM to act as a judge, comparing a ground truth and a prediction slidedeck.
    It now uses the same detailed prompt for both 'standard' and 'arena' modes.
    It also accepts optional full JSON summaries for more accurate judging.
    """
    if not judge_model:
        judge_model = "gemini-3-pro-preview"

    use_openrouter = _is_openrouter_model(judge_model)
    use_kimi = (_is_kimi_model(judge_model) and not use_openrouter) or use_openrouter
    use_openai = _is_openai_model(judge_model) and not use_kimi
    openai_client = None
    kimi_client = None  # OpenAI-compatible client: Moonshot or OpenRouter
    gemini_key = None

    # Resolve API key
    if not api_key and api_keys:
        if use_openai:
            api_key = api_keys.get("openai") or api_keys.get("openai_api_key")
        elif use_openrouter:
            api_key = api_keys.get("openrouter") or api_keys.get("openrouter_api_key")
        elif use_kimi:
            api_key = (
                api_keys.get("moonshot")
                or api_keys.get("moonshot_api_key")
                or api_keys.get("kimi")
                or api_keys.get("kimi_api_key")
            )
        else:
            api_key = api_keys.get("gemini") or api_keys.get("gemini_api_key")

    if use_openai:
        openai_client = _create_openai_client(api_key)
        if openai_client is None:
            return {"error": "OpenAI API key for judging not found."}
    elif use_openrouter:
        kimi_client = _create_openrouter_client(api_key)
        if kimi_client is None:
            return {"error": "OpenRouter API key for judging not found."}
    elif use_kimi:
        kimi_client = _create_kimi_client(api_key)
        if kimi_client is None:
            return {"error": "Kimi/Moonshot API key for judging not found."}
    else:
        if not api_key:
            keys = load_api_keys()
            api_key = keys.get("gemini_api_key") or keys.get("gemini")
        if not api_key:
            return {"error": "API key for judging not found."}
        gemini_key = api_key

    def _split_instruction_and_style(prompt: str) -> tuple[str, str]:
        if not isinstance(prompt, str):
            return "", ""
        if "Instruction:" in prompt and "Style Target:" in prompt:
            parts = prompt.split("Style Target:", 1)
            instruction = parts[0].replace("Instruction:", "").strip()
            style_target = parts[1].strip()
            return instruction, style_target
        return prompt.strip(), ""

    instruction_text, style_target_text = _split_instruction_and_style(user_prompt)

    print(f"Calling LLM Judge ({judge_model})...")

    # Request model id: strip an explicit "openrouter/" prefix if present.
    _compat_model = judge_model.split("openrouter/", 1)[-1] if judge_model.lower().startswith("openrouter/") else judge_model
    # Moonshot accepts a thinking toggle; OpenRouter exposes a unified
    # reasoning switch. Judges should answer, not deliberate for minutes.
    if use_openrouter:
        _compat_extra = {"reasoning": {"enabled": False}}
    elif _is_kimi_model(judge_model):
        _compat_extra = {"thinking": {"type": "disabled"}}
    else:
        _compat_extra = None

    def _call_kimi_chat_json(system_prompt: str, user_content, max_tokens: int = 2048) -> dict:
        response = kimi_client.chat.completions.create(
            model=_compat_model,
            messages=[
                {"role": "system", "content": system_prompt.strip()},
                {"role": "user", "content": user_content},
            ],
            max_tokens=max_tokens,
            extra_body=_compat_extra,
        )
        response_text = (response.choices[0].message.content or "").strip()
        parsed = extract_json_from_llm_response(response_text)
        if not isinstance(parsed, dict):
            raise ValueError(f"Failed to parse JSON from judge output ({_compat_model}): {response_text[:200]}")
        return parsed

    # Alignment suggestion, quality of prediction criteria. 
    # The prompt is now the same for both modes, with a strict rubric emphasizing content preservation and deterministic, explicit scoring.
    # Split rubrics: one for instruction following and one for visual preservation
    rubric_instruction_text = """
HARSH SCORING POLICY (very strict):
- Choose the lower score when uncertain between adjacent scores.
- For translation/summarization or other text edits requiring reasoning, semantic similarity is more important than exact wording.

INSTRUCTION_FOLLOWING score (0-5):
- 5: Every requested object/change exists and is exactly correct; nothing requested is missing or misapplied; no extra edits beyond the instruction.
- 4: All requested changes exist and are mostly correct; only a tiny inaccuracy that does not affect meaning.
- 3: Most requested changes exist but at least one is incomplete, incorrect, or missing detail.
- 2: Only some requested changes exist; notable misses or incorrect applications.
- 1: Requested changes largely not performed or substantially incorrect.
- 0: Contradicts or ignores the instruction entirely.
"""

    rubric_visual_text = """
HARSH SCORING POLICY (very strict):
- Penalize any unintended visual change to non-requested content (fonts, sizes, colors, positions, shapes, charts, images, tables, or slide structure).
- Choose the lower score when uncertain between adjacent scores.

VISUAL_Quality score (0-5):
- 5: No unintended changes to non-requested content; fonts, sizes, colors, positions, and objects match Ground Truth; structure preserved.
- 4: Visually very close to the Ground Truth; only imperceptible or negligible differences (e.g., sub-pixel alignment); no style drift.
- 3: Minor but noticeable visual differences (e.g., slight font weight/size/spacing shifts) without breaking layout.
- 2: Clear deviations (e.g., wrong fonts/sizes/colors, noticeable position shifts) or small layout issues.
- 1: Major deviations (overlap, off-canvas, broken layout) but still legible.
- 0: Severely broken or unreadable slide.
"""

    # Split the judgment into two focused sub-calls:
    #   1) JSON-only → instruction_following
    #   2) Images-only → visual_preservation

    def _judge_instruction_with_json() -> dict:
        try:
            # PHASE 2: Smart Diff Analysis
            # First, run the diff to find actual differences
            
            diff_result = None
            if original_ppt_json and modified_ppt_json:
                print("[InstructionJudge] Running smart diff analysis...")
                diff_result = ppt_processor.diff_pptx_json(
                    ground_truth_json=original_ppt_json,
                    prediction_json=modified_ppt_json,
                    initial_json=initial_ppt_json
                )
                
                # Quick optimization: if no differences and similarity is very high, return perfect score
                if not diff_result.get("has_differences") and diff_result.get("similarity_score", 0) > 0.9999:
                    print("[InstructionJudge] Perfect match detected! Returning score 5.")
                    return {
                        "instruction_following_score": 5,
                        "instruction_following_reason": "Prediction perfectly matches ground truth with no detectable differences.",
                        "diff_summary": "Perfect match (100% similarity)"
                    }
                
                # Check if we should use XML fallback
                instruction_part = instruction_text or user_prompt
                has_smartart = any(
                    slide.get("has_smartart", False) 
                    for slide in original_ppt_json.get("slides", [])
                )
                has_transitions = any(
                    slide.get("has_transition", False) 
                    for slide in original_ppt_json.get("slides", [])
                )
                
                if ppt_processor.should_use_xml_fallback(instruction_part, has_smartart, has_transitions):
                    print("[InstructionJudge] XML fallback required for SmartArt/animations/transitions")
                    # Fall through to use full JSON (would need XML integration for full support)
            
            system_prompt_json = f"""
You are a strict judge of INSTRUCTION FOLLOWING.

CRITICAL UNDERSTANDING:
- The "Instruction" is what the model/editor received (the user's request)
- The "Style Target" is YOUR evaluation rubric - the model DID NOT see this
- You will receive a FOCUSED DIFF showing only what changed between ground_truth and prediction
- Your job: Judge if the prediction's changes match the ground_truth's changes
- DO NOT compare prediction to initial - focus on whether prediction achieved ground_truth's outcome
- If the diff shows minimal differences, that's GOOD (high score)
- Ground Truth is ONE valid example, not the only correct answer

FLEXIBILITY:
- Accept different valid approaches (e.g., flags in a list vs rows is fine if they match the text)
- Exact positions/sizes don't matter unless the Instruction explicitly requires them
- Very small measurement variations (±1%) are acceptable for fonts/sizes due to rounding
- Z-order (layering) differences ARE significant and should be noted
- Focus on semantic properties: text content, font names, colors, structural changes, z-order

{rubric_instruction_text}
Output a single JSON object with:
- instruction_following_score (0-5)
- instruction_following_reason (one sentence, specific evidence comparing prediction to ground_truth)
"""

            instruction_part = instruction_text or user_prompt
            style_target_part = style_target_text or ""
            
            # Use diff if available, otherwise fall back to full JSON
            if diff_result:
                formatted_diff = ppt_processor.format_diff_for_judge(diff_result, instruction_part)
                prompt = f"""
--- USER INSTRUCTION (what the model received) ---
{instruction_part}

--- STYLE TARGET (your evaluation rubric - the model DID NOT see this) ---
{style_target_part if style_target_part else "Not provided - judge based on instruction intent only"}

--- SMART DIFF ANALYSIS (Prediction vs Ground Truth) ---
{formatted_diff}

CRITICAL COMPARISON INSTRUCTIONS:
The diff above shows ONLY the differences between prediction and ground_truth.
- If the diff shows "No differences" → Perfect match → Score 5
- If the diff shows differences in properties that the instruction requires → Score based on correctness
- Focus on whether prediction achieved the same semantic outcome as ground_truth

REMINDER: Judge if the prediction achieved the SEMANTIC INTENT of the Instruction.
The diff highlights what actually changed - use this to make an accurate judgment.
"""
            else:
                # Fallback to full JSON if diff failed
                combined_json = {
                    "initial": initial_ppt_json if initial_ppt_json is not None else "Not provided.",
                    "ground_truth": original_ppt_json if original_ppt_json is not None else "Not provided.",
                    "prediction": modified_ppt_json if modified_ppt_json is not None else "Not provided.",
                }
                
                prompt = f"""
--- USER INSTRUCTION (what the model received) ---
{instruction_part}

--- STYLE TARGET (your evaluation rubric - the model DID NOT see this) ---
{style_target_part if style_target_part else "Not provided - judge based on instruction intent only"}

--- JSON Summaries (initial, ground_truth, prediction) ---
```json
{json.dumps(combined_json, indent=2, sort_keys=True)}
```

CRITICAL COMPARISON INSTRUCTIONS:
1. "initial" = the original presentation BEFORE any changes
2. "ground_truth" = the correct target after applying the instruction (reference answer)
3. "prediction" = what the system produced

YOUR TASK: Compare "prediction" to "ground_truth" to judge instruction following.
- If prediction matches ground_truth, that's a HIGH score (4-5), even if both differ from initial
- If prediction matches initial but ground_truth differs, that's a LOW score (0-1) - the instruction wasn't applied
- The goal is for prediction to achieve the same outcome as ground_truth, NOT to leave it unchanged from initial

REMINDER: Judge if the prediction achieved the SEMANTIC INTENT of the Instruction by comparing it to Ground Truth.
The Style Target guides WHAT to check, not exact pixel-perfect replication.
Ground Truth is ONE valid example - accept other valid approaches that fulfill the instruction.
"""
            if use_openai:
                response = openai_client.responses.create(
                    model=judge_model,
                    input=[
                        {"role": "system", "content": [{"type": "input_text", "text": system_prompt_json.strip()}]},
                        {"role": "user", "content": [{"type": "input_text", "text": prompt.strip()}]},
                    ],
                )
                response_text = _extract_text_from_openai_response(response).strip()
            elif use_kimi:
                parsed = _call_kimi_chat_json(system_prompt_json, prompt.strip())
                response_text = json.dumps(parsed)
            else:
                _configure_gemini_client(judge_model, gemini_key)
                base_config = {
                    "response_mime_type": "application/json",
                    "top_p": 0,
                    "top_k": 1,
                    "candidate_count": 1,
                }
                generation_config = _build_gemini_generation_config(judge_model, base_config)
                model_kwargs = {"model_name": judge_model}
                if generation_config:
                    model_kwargs["generation_config"] = generation_config
                model = genai.GenerativeModel(**model_kwargs)
                response = model.generate_content([system_prompt_json, prompt])
                response_text = response.text.strip()

            parsed = extract_json_from_llm_response(response_text)
            
            if not parsed:
                print(f"[InstructionJudge] ERROR: Failed to parse JSON from response")
                print(f"[InstructionJudge] Response text: {response_text[:500]}")
                return {"instruction_following_score": 0, "instruction_following_reason": "Failed to parse response", "error": "JSON parse failed"}
            
            # Normalize possible key variants from the model
            # Use 'is not None' to avoid treating 0 as falsy
            score = parsed.get("instruction_following_score")
            if score is None:
                score = parsed.get("instruction_score")
            if score is None:
                score = parsed.get("instructionFollowingScore")
            if score is None:
                score = parsed.get("score")
            
            if score is None:
                print(f"[InstructionJudge] ERROR: No score found in parsed response")
                print(f"[InstructionJudge] Parsed keys: {list(parsed.keys())}")
                print(f"[InstructionJudge] Parsed content: {parsed}")
                return {"instruction_following_score": 0, "instruction_following_reason": "No score in response", "error": "No score found"}
            reason = (
                parsed.get("instruction_following_reason")
                or parsed.get("instruction_reason")
                or parsed.get("instructionFollowingReason")
                or parsed.get("reason")
                or ""
            )
            try:
                score = float(score)
            except Exception as e:
                print(f"[InstructionJudge] Warning: Could not convert score '{score}' to float: {e}")
                score = 0
            
            print(f"[InstructionJudge] Instruction score={score}, reason='{reason[:80] if reason else 'N/A'}...'")
            
            return {
                "instruction_following_score": score,
                "instruction_following_reason": reason,
                "suggested_improvement": parsed.get("suggested_improvement", parsed.get("improvement", "")),
            }
        except Exception as e:
            print(f"[InstructionJudge] ERROR: {e}")
            return {"error": f"Instruction judge error: {e}"}

    def _judge_visual_with_images() -> dict:
        try:
            system_prompt_visual = f"""
You are a judge of VISUAL/CONTENT QUALITY and PRESERVATION.

CRITICAL UNDERSTANDING:
- The "Instruction" is what the model/editor received
- The "Style Target" is YOUR evaluation rubric - the model DID NOT see this
- Ground Truth is ONE valid example - accept other valid visual approaches
- Focus on SEMANTIC correctness: Are the visual elements correct? No overlap? Readable?
- Exact positions/sizes don't matter unless the Instruction explicitly requires them
- You will only receive Ground Truth and Prediction slide images; compare them directly slide-by-slide.
- Use any provided Style Target guidance to check required visual cues strictly.

FLEXIBILITY:
- Different layouts achieving the same goal are acceptable (e.g., list vs grid)
- Small position variations are fine if elements are clear and non-overlapping
- Theme colors may vary slightly as long as they're harmonious
- "Approximately centered" or "well-aligned" is acceptable without pixel-perfection

{rubric_visual_text}
Output a single JSON object with:
- visual_quality_score (0-5)
- visual_quality_reason (one sentence, specific evidence about visual differences)
"""

            original_list = (original_slide_images_b64 or [])[:]
            if not original_list and original_slide_image_b64:
                original_list = [original_slide_image_b64]
            modified_list = (modified_slide_images_b64 or [])[:]
            if not modified_list and modified_slide_image_b64:
                modified_list = [modified_slide_image_b64]

            olen = len(original_list)
            mlen = len(modified_list)

            total_slides = min(olen, mlen) if min(olen, mlen) > 0 else max(olen, mlen)

            if total_slides == 0:
                return {
                    "visual_quality_score": 0.0,
                    "visual_quality_reason": "No slide images available for comparison.",
                    "error": "Missing images for visual judging.",
                }

            style_block = style_target_text.strip() if style_target_text else "Not provided."

            # Content-matched slide pairs as (gt_idx, pred_idx), with None for a slide
            # that has no counterpart. None means the decks line up, so slide i is
            # compared with slide i exactly as before.
            aligned_pairs = ppt_processor.visual_slide_pairs(original_ppt_json, modified_ppt_json, olen, mlen)

            def _pair_note(pairs):
                """Tell the judge which labeled slides belong together when positions differ."""
                if aligned_pairs is None:
                    return ""
                note = [
                    "",
                    "",
                    f"Slide matching: the decks differ in slide structure (Ground Truth has {olen} slides, "
                    f"Prediction has {mlen}), so slides are matched by content, not by position.",
                ]
                for gt_idx, pred_idx in pairs:
                    if gt_idx is None:
                        note.append(f"- PREDICTION_SLIDE_{pred_idx + 1} has no counterpart in the Ground Truth (extra slide)")
                    elif pred_idx is None:
                        note.append(f"- GROUND_TRUTH_SLIDE_{gt_idx + 1} has no counterpart in the Prediction (missing slide)")
                    elif gt_idx != pred_idx:
                        note.append(f"- GROUND_TRUTH_SLIDE_{gt_idx + 1} corresponds to PREDICTION_SLIDE_{pred_idx + 1}")
                note.append(
                    "Compare each Ground Truth slide with its matching Prediction slide. "
                    "An extra or missing slide is a change to slide structure."
                )
                return "\n".join(note)

            def _batch_label(batch):
                """Slide numbers in a batch; just the number when both decks agree on it."""
                parts = []
                for gt_idx, pred_idx in batch:
                    if gt_idx is None:
                        parts.append(f"Prediction {pred_idx + 1}")
                    elif pred_idx is None or pred_idx == gt_idx:
                        parts.append(str(gt_idx + 1))
                    else:
                        parts.append(f"{gt_idx + 1} (Prediction {pred_idx + 1})")
                return ", ".join(parts)

            def _flag_pairs_for_vlm(ssim_threshold, phash_threshold):
                """Slide pairs that differ enough to be worth showing the VLM."""
                candidates = aligned_pairs
                if candidates is None:
                    candidates = [(i, i) for i in range(min(olen, mlen))]
                flagged = []
                for pair in candidates:
                    gt_idx, pred_idx = pair
                    label = _batch_label([pair])
                    if gt_idx is None or pred_idx is None:
                        flagged.append(pair)
                        _log(f"[VisualJudge] Slide {label}: no counterpart → flagged for VLM", request_id)
                        continue
                    gt_img = _pil_from_b64(original_list[gt_idx])
                    pred_img = _pil_from_b64(modified_list[pred_idx])
                    if gt_img is None or pred_img is None:
                        flagged.append(pair)
                        _log(f"[VisualJudge] Slide {label}: missing image → flagged for VLM", request_id)
                        continue
                    a = _resize_for_metric(gt_img)
                    b = _resize_for_metric(pred_img)
                    if a.size != b.size:
                        b = b.resize(a.size, Image.BILINEAR)
                    ssim = _compute_ssim(_to_gray_np(a), _to_gray_np(b))
                    hdist = _hamming_distance64(_dct_8x8_hash(a), _dct_8x8_hash(b))
                    if ssim < ssim_threshold or hdist > phash_threshold:
                        flagged.append(pair)
                        _log(f"[VisualJudge] Slide {label}: SSIM={ssim:.4f}, pHashDist={hdist} → flagged", request_id)
                    else:
                        _log(f"[VisualJudge] Slide {label}: SSIM={ssim:.4f}, pHashDist={hdist} → likely identical", request_id)
                if not flagged:
                    flagged = [pair for pair in candidates if None not in pair][:5]
                    _log(f"[VisualJudge] No slides flagged; sampling slides {_batch_label(flagged)}", request_id)
                else:
                    _log(f"[VisualJudge] Flagged slides for VLM: {_batch_label(flagged)}", request_id)
                return flagged

            if use_openai or use_kimi:
                provider_name = "OpenAI" if use_openai else "Kimi"
                text_part_type = "input_text" if use_openai else "text"

                def _send_openai_compatible(content_items):
                    if use_openai:
                        response = openai_client.responses.create(
                            model=judge_model,
                            input=[
                                {"role": "system", "content": [{"type": "input_text", "text": system_prompt_visual.strip()}]},
                                {"role": "user", "content": content_items},
                            ],
                        )
                        response_text = _extract_text_from_openai_response(response).strip()
                        parsed = extract_json_from_llm_response(response_text)
                    else:
                        parsed = _call_kimi_chat_json(system_prompt_visual, content_items, max_tokens=1024)
                    if not isinstance(parsed, dict):
                        raise ValueError(f"Failed to parse JSON from {provider_name} judge output")
                    return parsed

                def _extract_visual_fields(parsed: dict):
                    vscore = (
                        parsed.get("visual_quality_score")
                        or parsed.get("visual_preservation_score")
                        or parsed.get("visual_score")
                        or parsed.get("visualQualityScore")
                        or parsed.get("score")
                    )
                    vreason = (
                        parsed.get("visual_quality_reason")
                        or parsed.get("visual_preservation_reason")
                        or parsed.get("visual_reason")
                        or parsed.get("visualQualityReason")
                        or parsed.get("reason")
                        or ""
                    )
                    try:
                        vscore = float(vscore)
                    except Exception:
                        vscore = 0.0
                    return vscore, vreason

                def _append_sequence(label: str, images: List[str], indices: List[int], content: List[dict]) -> None:
                    if not images:
                        return
                    for idx in indices:
                        if idx < len(images) and images[idx]:
                            data_url = f"data:image/png;base64,{images[idx]}"
                            content.append({"type": text_part_type, "text": f"{label}_SLIDE_{idx+1}"})
                            if use_openai:
                                content.append({"type": "input_image", "image_url": data_url})
                            else:
                                content.append({"type": "image_url", "image_url": {"url": data_url}})

                if total_slides < 5:
                    _log(f"[VisualJudge] Small deck mode ({provider_name}): GT={olen}, Pred={mlen}", request_id)
                    prompt_text = f"""
--- User Instruction ---
{instruction_text or user_prompt}

--- Style Target (judge rubric, unseen by the model under evaluation) ---
{style_block}

You are given two labeled image sequences:
- Ground Truth: the correct target deck to match (length: {olen} slides)
- Prediction: the candidate deck produced by the system (length: {mlen} slides){_pair_note(aligned_pairs)}

CRITICAL: Ground Truth is ONE valid example, not the only correct answer.
Judge if Prediction achieves the SEMANTIC INTENT shown by Ground Truth.
Accept different valid layouts/arrangements that fulfill the instruction and style target.
Focus on: correctness, no overlap, readability, theme consistency.
Small position/size variations are acceptable if elements are clear.

Judge visual quality by comparing PREDICTION to GROUND TRUTH.

Return only:
- visual_quality_score (0-5)
- visual_quality_reason (one sentence, specific evidence about visual differences)
"""
                    content = [{"type": text_part_type, "text": prompt_text.strip()}]
                    _append_sequence("GROUND_TRUTH", original_list, list(range(olen)), content)
                    _append_sequence("PREDICTION", modified_list, list(range(mlen)), content)
                    parsed = _send_openai_compatible(content)
                    vscore, vreason = _extract_visual_fields(parsed)
                    _log(f"[VisualJudge] Small deck ({provider_name}) result: score={vscore}, reason='{vreason}'", request_id)
                    return {"visual_quality_score": vscore, "visual_quality_reason": vreason}

                SSIM_THRESHOLD = float(os.environ.get("VISUAL_SSIM_THRESH", "0.9995"))
                PHASH_DIST_THRESHOLD = int(os.environ.get("VISUAL_PHASH_THRESH", "2"))
                differing_pairs = _flag_pairs_for_vlm(SSIM_THRESHOLD, PHASH_DIST_THRESHOLD)

                chunk_size = 5
                batches = [differing_pairs[i:i + chunk_size] for i in range(0, len(differing_pairs), chunk_size)]
                if len(batches) >= 2 and len(batches[-1]) < 3:
                    batches[-2].extend(batches[-1])
                    batches = batches[:-1]
                _log(f"[VisualJudge] Created {len(batches)} batch(es): {[_batch_label(b) for b in batches]}", request_id)

                batch_scores: List[float] = []
                batch_reasons: List[str] = []
                for batch in batches:
                    gt_indices = [g for g, _ in batch if g is not None]
                    pred_indices = [p for _, p in batch if p is not None]
                    prompt_text = f"""
--- User Instruction ---
{instruction_text or user_prompt}

--- Style Target (judge rubric, unseen by the model under evaluation) ---
{style_block}

You are given two labeled image sequences for slides: {_batch_label(batch)}
- Ground Truth: the correct target deck to match (slides available in this batch: {len(gt_indices)})
- Prediction: the candidate deck produced by the system (slides available in this batch: {len(pred_indices)}){_pair_note(batch)}

CRITICAL: Ground Truth is ONE valid example, not the only correct answer.
Judge if Prediction achieves the SEMANTIC INTENT shown by Ground Truth.
Accept different valid layouts/arrangements that fulfill the instruction and style target.
Focus on: correctness, no overlap, readability, theme consistency.
Small position/size variations are acceptable if elements are clear.

Judge visual quality by comparing PREDICTION to GROUND TRUTH.
Return only:
- visual_quality_score (0-5)
- visual_quality_reason (one sentence, specific evidence about visual differences)
"""
                    content = [{"type": text_part_type, "text": prompt_text.strip()}]
                    _append_sequence("GROUND_TRUTH", original_list, gt_indices, content)
                    _append_sequence("PREDICTION", modified_list, pred_indices, content)
                    parsed = _send_openai_compatible(content)
                    vscore, vreason = _extract_visual_fields(parsed)
                    batch_scores.append(vscore)
                    batch_reasons.append(vreason)
                    _log(f"[VisualJudge] Batch slides {_batch_label(batch)} ({provider_name}) → score={vscore}, reason='{vreason}'", request_id)

                if batch_scores:
                    min_score = round(min(batch_scores), 2)
                    reason_text = " | ".join([r for r in batch_reasons if r][:3])
                    _log(f"[VisualJudge] Final visual score ({provider_name}, min of batches) = {min_score}", request_id)
                    return {"visual_quality_score": min_score, "visual_quality_reason": reason_text}
                return {"visual_quality_score": 0.0, "visual_quality_reason": "no_batches_evaluated"}

            # Gemini / Google path
            _configure_gemini_client(judge_model, gemini_key)
            base_generation_config = {
                "response_mime_type": "application/json",
                "temperature": 0.1,
                "top_p": 0,
                "top_k": 1,
                "candidate_count": 1,
            }
            generation_config = _build_gemini_generation_config(
                judge_model,
                base_generation_config,
                use_high_res_media=True,
            )
            model_kwargs = {"model_name": judge_model}
            if generation_config:
                model_kwargs["generation_config"] = generation_config
            model = genai.GenerativeModel(**model_kwargs)

            if total_slides < 5:
                _log(f"[VisualJudge] Small deck mode: judging all slides at once (GT={olen}, Pred={mlen})", request_id)
                parts = []
                if original_list:
                    parts.append("--- GROUND TRUTH (Correct target deck) ---")
                for idx, b64 in enumerate(original_list, start=1):
                    if b64:
                        parts.append(f"GROUND_TRUTH_SLIDE_{idx}")
                        parts.append({"inline_data": {"mime_type": "image/png", "data": b64}})
                if modified_list:
                    parts.append("--- PREDICTION (Candidate deck to evaluate) ---")
                for idx, b64 in enumerate(modified_list, start=1):
                    if b64:
                        parts.append(f"PREDICTION_SLIDE_{idx}")
                        parts.append({"inline_data": {"mime_type": "image/png", "data": b64}})

                prompt = f"""
--- User Instruction ---
{instruction_text or user_prompt}

--- Style Target (judge rubric, unseen by the model under evaluation) ---
{style_block}

You are given two labeled image sequences:
- Ground Truth: the correct target deck to match (length: {olen} slides)
- Prediction: the candidate deck produced by the system (length: {mlen} slides){_pair_note(aligned_pairs)}

CRITICAL: Ground Truth is ONE valid example, not the only correct answer.
Judge if Prediction achieves the SEMANTIC INTENT shown by Ground Truth.
Accept different valid layouts/arrangements that fulfill the instruction and style target.
Focus on: correctness, no overlap, readability, theme consistency.
Small position/size variations are acceptable if elements are clear.

Judge visual quality by comparing PREDICTION to GROUND TRUTH.

Return only:
- visual_quality_score (0-5)
- visual_quality_reason (one sentence, specific evidence about visual differences)
"""
                response = model.generate_content([system_prompt_visual, prompt] + parts)
                parsed = extract_json_from_llm_response(response.text.strip())
                vscore = (
                    parsed.get("visual_quality_score")
                    or parsed.get("visual_preservation_score")
                    or parsed.get("visual_score")
                    or parsed.get("visualQualityScore")
                    or parsed.get("score")
                )
                vreason = (
                    parsed.get("visual_quality_reason")
                    or parsed.get("visual_preservation_reason")
                    or parsed.get("visual_reason")
                    or parsed.get("visualQualityReason")
                    or parsed.get("reason")
                    or ""
                )
                try:
                    vscore = float(vscore)
                except Exception:
                    vscore = 0
                _log(f"[VisualJudge] Small deck result: score={vscore}, reason='{vreason}'", request_id)
                return {
                    "visual_quality_score": vscore,
                    "visual_quality_reason": vreason,
                }

            SSIM_THRESHOLD = float(os.environ.get("VISUAL_SSIM_THRESH", "0.995"))
            PHASH_DIST_THRESHOLD = int(os.environ.get("VISUAL_PHASH_THRESH", "2"))

            differing_pairs = _flag_pairs_for_vlm(SSIM_THRESHOLD, PHASH_DIST_THRESHOLD)

            chunk_size = 5
            batches = [differing_pairs[i:i+chunk_size] for i in range(0, len(differing_pairs), chunk_size)]
            if len(batches) >= 2 and len(batches[-1]) < 3:
                batches[-2].extend(batches[-1])
                batches = batches[:-1]
            _log(f"[VisualJudge] Created {len(batches)} batch(es): {[_batch_label(b) for b in batches]}", request_id)

            batch_scores = []
            batch_reasons = []
            for batch in batches:
                gt_indices = [g for g, _ in batch if g is not None]
                pred_indices = [p for _, p in batch if p is not None]
                parts = []
                if original_list:
                    parts.append("--- GROUND TRUTH (Correct target deck) ---")
                for idx in gt_indices:
                    if idx < olen and original_list[idx]:
                        parts.append(f"GROUND_TRUTH_SLIDE_{idx+1}")
                        parts.append({"inline_data": {"mime_type": "image/png", "data": original_list[idx]}})
                if modified_list:
                    parts.append("--- PREDICTION (Candidate deck to evaluate) ---")
                for idx in pred_indices:
                    if idx < mlen and modified_list[idx]:
                        parts.append(f"PREDICTION_SLIDE_{idx+1}")
                        parts.append({"inline_data": {"mime_type": "image/png", "data": modified_list[idx]}})

                prompt = f"""
--- User Instruction ---
{instruction_text or user_prompt}

--- Style Target (judge rubric, unseen by the model under evaluation) ---
{style_block}

You are given two labeled image sequences for slides: {_batch_label(batch)}
- Ground Truth: the correct target deck to match (slides available in this batch: {len(gt_indices)})
- Prediction: the candidate deck produced by the system (slides available in this batch: {len(pred_indices)}){_pair_note(batch)}

CRITICAL: Ground Truth is ONE valid example, not the only correct answer.
Judge if Prediction achieves the SEMANTIC INTENT shown by Ground Truth.
Accept different valid layouts/arrangements that fulfill the instruction and style target.
Focus on: correctness, no overlap, readability, theme consistency.
Small position/size variations are acceptable if elements are clear.

Judge visual quality by comparing PREDICTION to GROUND TRUTH.
Return only:
- visual_quality_score (0-5)
- visual_quality_reason (one sentence, specific evidence about visual differences)
"""
                response = model.generate_content([system_prompt_visual, prompt] + parts)
                parsed = extract_json_from_llm_response(response.text.strip())
                vscore = (
                    parsed.get("visual_quality_score")
                    or parsed.get("visual_preservation_score")
                    or parsed.get("visual_score")
                    or parsed.get("visualQualityScore")
                    or parsed.get("score")
                )
                vreason = (
                    parsed.get("visual_quality_reason")
                    or parsed.get("visual_preservation_reason")
                    or parsed.get("visual_reason")
                    or parsed.get("visualQualityReason")
                    or parsed.get("reason")
                    or ""
                )
                try:
                    vscore = float(vscore)
                except Exception:
                    vscore = 0
                batch_scores.append(vscore)
                batch_reasons.append(vreason)
                _log(f"[VisualJudge] Batch slides {_batch_label(batch)} → score={vscore}, reason='{vreason}'", request_id)

            if batch_scores:
                min_score = round(min(batch_scores), 2)
                reason_text = " | ".join([r for r in batch_reasons if r][:3])
                _log(f"[VisualJudge] Final visual score (min of batches) = {min_score}", request_id)
                return {"visual_quality_score": min_score, "visual_quality_reason": reason_text}
            else:
                return {"visual_quality_score": 0, "visual_quality_reason": "no_batches_evaluated"}
        except Exception as e:
            return {"error": f"Visual judge error: {e}"}

    # For arena, use a more powerful model by default if not specified
    try:
        instr = _judge_instruction_with_json()
        vis = _judge_visual_with_images()

        judge_result = {
            "instruction_following_score": instr.get("instruction_following_score", 0),
            "instruction_following_reason": instr.get("instruction_following_reason", ""),
            "visual_quality_score": vis.get("visual_quality_score", 0),
            "visual_quality_reason": vis.get("visual_quality_reason", ""),
            "suggested_improvement": instr.get("suggested_improvement", ""),
        }

        # Calculate average score if not present
        if 'overall_score' not in judge_result and all(k in judge_result for k in ['instruction_following_score', 'visual_quality_score', 'content_accuracy_score']):
            scores = [
                judge_result['instruction_following_score'],
                judge_result['visual_quality_score'],
                judge_result['content_accuracy_score']
            ]
            judge_result['overall_score'] = round(sum(scores) / len(scores), 2)

        # Ensure required keys exist with defaults
        if 'instruction_following_reason' not in judge_result:
            judge_result['instruction_following_reason'] = ""
        if 'visual_quality_reason' not in judge_result:
            judge_result['visual_quality_reason'] = ""

        return judge_result

    except Exception as e:
        error_message = f"Error in call_llm_judge: {str(e)}"
        print(error_message)
        return {"error": error_message}
