"""
OCR utility for extracting structured data from academic certificates.

Pipeline:
1. OpenCV preprocessing (grayscale → denoise → adaptive threshold)
2. Tesseract OCR for raw text extraction
3. Regex parsing to extract structured fields

Includes mock data for e2e testing without real images.
"""

import os
import re
from typing import Optional

from app.core.logging import get_logger

logger = get_logger("ai.ocr")

# ── Mock certificate data (for e2e testing) ──────────────────

MOCK_CERTIFICATES: dict[str, dict] = {
    "/uploads/grade12_cert_abebe.pdf": {
        "student_name": "Abebe Kebede",
        "admission_number": "MOE-2025-NAT-001",
        "exam_year": 2025,
        "subjects": {
            "Mathematics": 92,
            "Physics": 85,
            "Chemistry": 78,
            "Biology": 80,
            "English": 88,
            "Civics": 75,
        },
        "has_stamp": True,
        "has_signature": True,
    },
    "/uploads/grade12_cert_tigist.pdf": {
        "student_name": "Tigist Haile",
        "admission_number": "MOE-2025-SOC-001",
        "exam_year": 2025,
        "subjects": {
            "History": 88,
            "Geography": 82,
            "Economics": 90,
            "English": 85,
            "Civics": 79,
            "Mathematics": 70,
        },
        "has_stamp": True,
        "has_signature": True,
    },
    "/uploads/grade12_cert_fraud.pdf": {
        "student_name": "Abebe Kebede",
        "admission_number": "MOE-2025-NAT-001",
        "exam_year": 2025,
        "subjects": {
            "Mathematics": 99,
            "Physics": 95,
            "Chemistry": 90,
            "Biology": 88,
            "English": 95,
            "Civics": 90,
        },
        "has_stamp": False,
        "has_signature": True,
    },
    "/uploads/grade12_cert_no_stamp.pdf": {
        "student_name": "Dawit Mekonnen",
        "admission_number": "MOE-2025-NAT-003",
        "exam_year": 2025,
        "subjects": {
            "Mathematics": 76,
            "Physics": 72,
            "Chemistry": 68,
            "Biology": 74,
            "English": 80,
            "Civics": 70,
        },
        "has_stamp": False,
        "has_signature": False,
    },
}

# ── Known ESSLCE subjects ────────────────────────────────────
# Used for fuzzy matching against noisy OCR output.

KNOWN_SUBJECTS = [
    "English", "Maths", "Mathematics", "Physics", "Chemistry",
    "Biology", "Civics", "Aptitude", "History", "Geography",
    "Economics", "Amharic",
]


def _preprocess_image(file_path: str):
    """
    Load an image and apply OpenCV preprocessing to improve OCR accuracy.
    Returns a preprocessed OpenCV image ready for Tesseract, or None on failure.
    """
    try:
        import cv2
        import numpy as np

        if file_path.lower().endswith(".pdf"):
            from pdf2image import convert_from_path
            pages = convert_from_path(file_path, dpi=300)
            if not pages:
                return None
            # Use the first page
            img = cv2.cvtColor(np.array(pages[0]), cv2.COLOR_RGB2BGR)
        else:
            img = cv2.imread(file_path)

        if img is None:
            logger.error("Failed to load image: %s", file_path)
            return None

        # 1. Convert to grayscale
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # 2. Resize if the image is small (Tesseract works best at 300+ DPI)
        h, w = gray.shape
        if w < 2000:
            scale = 2000 / w
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

        # 3. Otsu thresholding — best results for ESSLCE certificates
        #    Automatically finds the optimal threshold to separate text from background
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        return thresh

    except ImportError as e:
        logger.warning("Missing dependency for image preprocessing: %s", e)
        return None
    except Exception as e:
        logger.error("Image preprocessing failed: %s", e)
        return None


def _extract_text_tesseract(preprocessed_img) -> Optional[str]:
    """
    Run Tesseract OCR on a preprocessed OpenCV image.
    """
    try:
        import pytesseract
        # psm 3: Fully automatic page segmentation — best for certificate layouts
        text = pytesseract.image_to_string(preprocessed_img, config="--psm 3")
        return text
    except ImportError:
        logger.warning("pytesseract not installed")
        return None
    except Exception as e:
        logger.error("Tesseract OCR failed: %s", e)
        return None


def _fuzzy_match_subject(word: str) -> Optional[str]:
    """
    Try to match a word against known ESSLCE subjects.
    Handles common OCR errors like 'Mathis' → 'Maths', 'Physies' → 'Physics'.
    """
    word_lower = word.lower().strip()
    for subj in KNOWN_SUBJECTS:
        subj_lower = subj.lower()
        # Exact match
        if word_lower == subj_lower:
            return subj
        # Starts-with match (at least 4 chars)
        if len(word_lower) >= 4 and subj_lower.startswith(word_lower[:4]):
            return subj
        # Contains match for longer subject names
        if len(word_lower) >= 5 and word_lower[:5] in subj_lower:
            return subj
    return None


def parse_certificate_text(raw_text: str) -> dict:
    """
    Parse raw OCR text and extract structured certificate data using regex.
    """
    data: dict = {
        "student_name": None,
        "admission_number": None,
        "exam_year": None,
        "subjects": {},
        "has_stamp": False,
        "has_signature": False,
    }

    # ── Student name ──────────────────────────────────────
    # Pattern 1: "Name: <name>" or "Student Name: <name>"
    name_match = re.search(
        r"(?:student\s*)?name\s*[:\-]\s*([A-Za-z\s]+)",
        raw_text, re.IGNORECASE
    )
    if not name_match:
        # Pattern 2: ESSLCE format "certify that <NAME>"
        name_match = re.search(
            r"certify\s+that\s+([A-Z][A-Z\s]+)",
            raw_text
        )
    if name_match:
        clean_name = re.sub(r'[\r\n\.]+', ' ', name_match.group(1)).strip()
        # Remove trailing noise words that aren't part of a name
        clean_name = re.sub(r'\s+(VS|Sea|has|taken|Has).*$', '', clean_name, flags=re.IGNORECASE).strip()
        if len(clean_name) >= 3:
            data["student_name"] = clean_name

    # ── Admission / Certificate number ────────────────────
    # Look for "Certificate No: 2955397" or similar
    adm_match = re.search(
        r"(?:certificate|admission|registration|document)\s*(?:no|number|#)?\s*[:\-]?\s*(\d[\w\-]*)",
        raw_text, re.IGNORECASE
    )
    if not adm_match:
        # Fallback: look for "No NNNNNNN" or "Ne NNNNNNN" (OCR misread of "No")
        adm_match = re.search(r"(?:No|Ne|N[oe])\s+(\d{5,})", raw_text)
    if not adm_match:
        # Last resort: find any standalone 7-digit number
        adm_match = re.search(r"\b(\d{7})\b", raw_text)
    if adm_match:
        data["admission_number"] = adm_match.group(1).strip()

    # ── Exam year ─────────────────────────────────────────
    # Look for 4-digit year near exam context words
    year_match = re.search(
        r"(?:in\s+the\s*=?\s*|exam\s*year\s*[:\-]?\s*|session\s*[:\-]?\s*)(20\d{2})",
        raw_text, re.IGNORECASE
    )
    if not year_match:
        # Fallback: find any 4-digit year between 2015-2030
        year_match = re.search(r"\b(20[12]\d)\b", raw_text)
    if year_match:
        data["exam_year"] = int(year_match.group(1))

    # ── Subject scores ────────────────────────────────────
    # Strategy: scan each line for a known subject name followed by a number
    for line in raw_text.split("\n"):
        line = line.strip()
        if not line:
            continue

        # Try to find a subject + score on the same line
        # Pattern: "SubjectName ... NN" with possible trailing noise (: . , etc)
        line_match = re.match(
            r"^([A-Za-z\s\(\)]+?)\s+.*?(\d{2,3})\s*[:\.\,;\s]*$",
            line
        )
        if line_match:
            raw_subj = line_match.group(1).strip()
            # Remove parenthetical qualifiers like "(Natural)"
            raw_subj = re.sub(r'\(.*?\)', '', raw_subj).strip()
            matched_subj = _fuzzy_match_subject(raw_subj)
            if matched_subj:
                score = int(line_match.group(2))
                if 0 <= score <= 100:
                    # Normalize "Maths" to "Mathematics"
                    if matched_subj == "Maths":
                        matched_subj = "Mathematics"
                    data["subjects"][matched_subj] = score
                    continue

        # Simpler pattern: "Subject NN" with optional trailing noise
        simple_match = re.match(
            r"^([A-Za-z]+(?:\s*\([A-Za-z]+\))?)\s+(\d{2,3})\s*[:\.\,;\s]*$",
            line
        )
        if simple_match:
            raw_subj = re.sub(r'\(.*?\)', '', simple_match.group(1)).strip()
            matched_subj = _fuzzy_match_subject(raw_subj)
            if matched_subj:
                score = int(simple_match.group(2))
                if 0 <= score <= 100:
                    if matched_subj == "Maths":
                        matched_subj = "Mathematics"
                    data["subjects"][matched_subj] = score

    # ── Authenticity markers ──────────────────────────────
    text_lower = raw_text.lower()
    data["has_stamp"] = any(
        kw in text_lower
        for kw in ["stamp", "seal", "neaea seal", "official stamp", "ministry seal",
                    "bears the neaea", "embossed"]
    )
    data["has_signature"] = any(
        kw in text_lower
        for kw in ["signature", "signed by", "authorized", "director",
                    "vice director", "certifying officer"]
    )

    return data


def extract_certificate_data(file_path: str) -> dict:
    """
    Main entry point: Extract structured data from a certificate file.

    1. Checks if file_path matches a mock certificate (for testing).
    2. Preprocesses image with OpenCV (grayscale, denoise, threshold).
    3. Runs Tesseract OCR to extract raw text.
    4. Parses raw text with regex to get structured data.
    """
    # Check mock data first
    if file_path in MOCK_CERTIFICATES:
        logger.info("Using mock certificate data for: %s", file_path)
        return MOCK_CERTIFICATES[file_path].copy()

    if not os.path.exists(file_path):
        logger.warning("Certificate file not found: %s", file_path)
        return _empty_result()

    # Step 1: Preprocess the image
    preprocessed = _preprocess_image(file_path)
    if preprocessed is None:
        logger.warning("Image preprocessing failed for: %s", file_path)
        return _empty_result()

    # Step 2: Run Tesseract OCR
    raw_text = _extract_text_tesseract(preprocessed)
    if not raw_text or len(raw_text.strip()) < 10:
        logger.warning("Tesseract produced no usable text for: %s", file_path)
        return _empty_result()

    logger.info("Tesseract OCR extracted %d chars from: %s", len(raw_text), file_path)
    logger.debug("Raw OCR text:\n%s", raw_text[:500])

    # Step 3: Parse the text with regex
    return parse_certificate_text(raw_text)


def _empty_result() -> dict:
    """Return an empty extraction result."""
    return {
        "student_name": None,
        "admission_number": None,
        "exam_year": None,
        "subjects": {},
        "has_stamp": False,
        "has_signature": False,
    }
