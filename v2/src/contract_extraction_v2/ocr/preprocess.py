from __future__ import annotations

from typing import Any

from PIL import Image, ImageEnhance, ImageFilter, ImageOps


def _rotate_from_exif(image: Image.Image) -> Image.Image:
    return ImageOps.exif_transpose(image)


def deskew(image: Image.Image) -> tuple[Image.Image, float]:
    """Deskew extension point.

    V2 deliberately avoids a mandatory OpenCV dependency.  A future optional
    detector can return an adjusted image and angle without changing callers.
    """

    return image, 0.0


def preprocess_normal(image: Image.Image) -> Image.Image:
    image = _rotate_from_exif(image).convert("RGB")
    image, _ = deskew(image)
    return ImageEnhance.Sharpness(image).enhance(1.08)


def preprocess_retry(image: Image.Image) -> Image.Image:
    image = _rotate_from_exif(image).convert("RGB")
    image, _ = deskew(image)
    gray = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=0.4)
    gray = ImageEnhance.Contrast(gray).enhance(1.30)
    gray = ImageEnhance.Sharpness(gray).enhance(1.45)
    return gray.convert("RGB")


def preprocess_signature(image: Image.Image) -> Image.Image:
    image = _rotate_from_exif(image).convert("RGB")
    red, green, blue = image.split()
    # Dark text is shared by all channels while red seals are strongest in the
    # red channel.  Using the darker green/blue average suppresses the seal
    # without hard thresholding dates and model numbers.
    seal_reduced = Image.blend(green, blue, 0.5)
    seal_reduced = ImageOps.autocontrast(seal_reduced, cutoff=0.3)
    seal_reduced = ImageEnhance.Contrast(seal_reduced).enhance(1.45)
    seal_reduced = seal_reduced.filter(ImageFilter.UnsharpMask(radius=1.2, percent=125, threshold=3))
    return seal_reduced.convert("RGB")


def preprocess_by_name(image: Image.Image, name: str) -> Image.Image:
    handlers: dict[str, Any] = {
        "normal": preprocess_normal,
        "retry": preprocess_retry,
        "signature": preprocess_signature,
    }
    return handlers.get(name, preprocess_normal)(image)
