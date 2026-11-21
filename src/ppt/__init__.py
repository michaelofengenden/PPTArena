from .ppt_processor import (
    pptx_to_json,
    diff_pptx_json,
    should_use_xml_fallback,
    format_diff_for_judge,
    extract_xml_from_pptx,
    create_modified_pptx,
    extract_specific_xml_from_pptx,
    export_slides_to_images,
    convert_pptx_to_pdf,
    convert_pptx_to_base64_images,
    validate_xml,
    attempt_repair_xml,
    extract_text_from_shape
)
from .slide_alignment import align_slides, visual_slide_pairs
