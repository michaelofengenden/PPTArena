import os
import shutil
import subprocess
import base64
import tempfile
from pathlib import Path
import re
import threading

# LibreOffice runs one at a time by default, as concurrent runs have crashed on
# macOS with DeploymentException. Each run gets its own profile folder, so several
# at once can work: set PPT_RENDER_CONCURRENCY to allow that many.
_libreoffice_lock = threading.BoundedSemaphore(max(1, int(os.environ.get("PPT_RENDER_CONCURRENCY", "1"))))

def _run_soffice_with_retry(cmd, max_retries=3):
    import time
    for attempt in range(max_retries):
        try:
            with _libreoffice_lock:
                # Optionally kill existing headless processes if they are hanging
                # subprocess.run(['pkill', '-f', 'soffice --headless'], stderr=subprocess.DEVNULL)
                subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            return True
        except subprocess.CalledProcessError as e:
            if attempt < max_retries - 1:
                print(f"LibreOffice failed (attempt {attempt+1}): {e}. Retrying in 2 seconds...")
                time.sleep(2)
            else:
                raise e
    return False

def convert_pptx_to_pdf(pptx_filepath, output_folder):
    """Converts a .pptx file to PDF using LibreOffice."""
    libreoffice_exec = None
    for name in ['soffice', 'libreoffice']:
        if shutil.which(name):
            libreoffice_exec = name
            break

    if not libreoffice_exec:
        print("ERROR: Could not find LibreOffice for PDF conversion.")
        return None

    abs_pptx_filepath = os.path.abspath(pptx_filepath)
    abs_output_folder = os.path.abspath(output_folder)
    Path(abs_output_folder).mkdir(parents=True, exist_ok=True)

    user_profile = tempfile.mkdtemp()
    user_installation = f"-env:UserInstallation=file://{user_profile}"

    try:
        cmd = [
            libreoffice_exec,
            user_installation,
            '--headless',
            '--nologo',
            '--nodefault',
            '--norestore',
            '--convert-to', 'pdf',
            abs_pptx_filepath,
            '--outdir', abs_output_folder
        ]
        _run_soffice_with_retry(cmd)
        pdf_path = Path(abs_output_folder) / (Path(abs_pptx_filepath).stem + '.pdf')
        if pdf_path.exists():
            return str(pdf_path)
    except Exception as e:
        print(f"Error converting PPTX to PDF: {e}")
    finally:
        shutil.rmtree(user_profile, ignore_errors=True)
    return None

def export_slides_to_images(pptx_filepath, output_folder):
    """
    Converts each slide of a .pptx file to PNG images using LibreOffice if
    available. Returns a list of file paths to the generated images. If
    conversion fails, an empty list is returned.
    """
    # Try to find a valid executable for LibreOffice
    libreoffice_exec = None
    for name in ['soffice', 'libreoffice']:
        if shutil.which(name):
            libreoffice_exec = name
            break

    if not libreoffice_exec:
        print("ERROR: Could not find 'soffice' or 'libreoffice' command.")
        print("Please ensure LibreOffice is installed and that its program directory is in your system's PATH.")
        return []

    # Use abspath to handle relative paths correctly
    abs_pptx_filepath = os.path.abspath(pptx_filepath)
    abs_output_folder = os.path.abspath(output_folder)
    Path(abs_output_folder).mkdir(parents=True, exist_ok=True)

    user_profile = tempfile.mkdtemp()
    user_installation = f"-env:UserInstallation=file://{user_profile}"

    try:
        # On macOS, the command might be different, but 'libreoffice' is standard for PATH
        # The user might need to symlink /Applications/LibreOffice.app/Contents/MacOS/soffice
        cmd = [
            libreoffice_exec,
            user_installation,
            '--headless',
            '--nologo',
            '--nodefault',
            '--norestore',
            '--convert-to', 'png',
            abs_pptx_filepath,
            '--outdir', abs_output_folder
        ]
        # Increased timeout to handle large files
        # Snapshot current images to filter only newly generated files
        pre_existing = set(str(p) for p in Path(abs_output_folder).glob('*.png'))
        _run_soffice_with_retry(cmd)
        post_existing = set(str(p) for p in Path(abs_output_folder).glob('*.png'))
        newly_created = list(post_existing - pre_existing)
        # If LibreOffice overwrote in-place (no set difference), fall back to all PNGs in the folder
        if not newly_created:
            newly_created = [str(p) for p in Path(abs_output_folder).glob('*.png')]

    except FileNotFoundError:
        # This is a fallback, should not be hit if shutil.which() is accurate.
        print(f"ERROR: The '{libreoffice_exec}' command was not found, even though it was detected.")
        print("Please ensure LibreOffice is installed and that its program directory is in your system's PATH.")
        return []
    except subprocess.TimeoutExpired:
        print("ERROR: Timeout expired while converting slides with LibreOffice. The presentation may be too large or complex.")
        return []
    except Exception as e:
        print(f"Error exporting slides using LibreOffice: {e}")
        # Capture stderr for more detailed error info if available
        if hasattr(e, 'stderr') and e.stderr:
            print(f"LibreOffice stderr: {e.stderr.decode()}")
        return []
    finally:
        shutil.rmtree(user_profile, ignore_errors=True)

    # Find generated files for this conversion only, sort naturally so slide10 comes after slide9
    def get_slide_num(f):
        match = re.search(r'(\d+)', os.path.basename(f))
        return int(match.group(1)) if match else -1

    # Fallback if detection failed
    candidate_paths = newly_created if 'newly_created' in locals() else [str(p) for p in Path(abs_output_folder).glob('*.png')]
    candidate_paths = [p for p in candidate_paths if os.path.exists(p)]
    candidate_paths.sort(key=get_slide_num)

    # If we only got a single PNG, LibreOffice likely exported only the first slide.
    # Fallback: export to PDF and rasterize pages to PNGs using pdftoppm if available.
    if len(candidate_paths) <= 1:
        try:
            pdf_path = convert_pptx_to_pdf(abs_pptx_filepath, abs_output_folder)
            pdftoppm_exec = shutil.which('pdftoppm')
            if pdf_path and pdftoppm_exec:
                prefix = os.path.join(abs_output_folder, 'slide')
                # -png writes slide-1.png, slide-2.png, ...
                subprocess.run([pdftoppm_exec, '-png', pdf_path, prefix], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
                pngs = sorted([str(p) for p in Path(abs_output_folder).glob('slide-*.png')], key=get_slide_num)
                if pngs:
                    return pngs
        except Exception as e:
            print(f"PDF fallback image export failed: {e}")

    return candidate_paths

def convert_pptx_to_base64_images(pptx_filepath):
    """Converts each slide of a .pptx file to base64 encoded PNG images."""
    temp_dir = tempfile.mkdtemp()
    images = export_slides_to_images(pptx_filepath, temp_dir)
    base64_images = []
    for img_path in images:
        try:
            with open(img_path, 'rb') as f:
                img_str = base64.b64encode(f.read()).decode('utf-8')
                base64_images.append(f"data:image/png;base64,{img_str}")
        except Exception as e:
            print(f"Error encoding image {img_path} to base64: {e}")
    shutil.rmtree(temp_dir, ignore_errors=True)
    return base64_images
