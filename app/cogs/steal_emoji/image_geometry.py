"""Fit visible artwork consistently across every animation frame."""

from PIL import Image, ImageFilter


def fit_frames(
    frames: list[Image.Image],
    size: int,
    *,
    upscale: bool = False,
    square: bool = False,
) -> tuple[list[Image.Image], bool]:
    """Trim transparent borders, preserve proportions, and optionally fill a square.

    Use one bounding box for the whole animation so moving artwork keeps its
    position and scale. Blank frames are retained for timing and transparency.
    """
    bounds = [box for frame in frames if (box := frame.getchannel("A").getbbox())]
    if not bounds:
        raise ValueError("This image is completely transparent.")
    box = (
        min(bound[0] for bound in bounds), min(bound[1] for bound in bounds),
        max(bound[2] for bound in bounds), max(bound[3] for bound in bounds),
    )
    width, height = box[2] - box[0], box[3] - box[1]
    scale = size / max(width, height)
    if not upscale:
        scale = min(1.0, scale)
    dimensions = (max(1, round(width * scale)), max(1, round(height * scale)))
    original = frames[0].size
    changed = box != (0, 0, *original) or dimensions != original
    changed = changed or (square and original != (size, size))
    if not changed:
        return frames, False

    fitted = []
    for frame in frames:
        image = frame.crop(box)
        if image.size != dimensions:
            image = image.resize(dimensions, Image.Resampling.LANCZOS)
            if scale > 1:
                # Recover edge contrast after enlargement without changing alpha
                # or inventing detail. Lanczos preserves smooth proportions.
                alpha = image.getchannel("A")
                image = image.filter(ImageFilter.UnsharpMask(radius=0.8, percent=90, threshold=3))
                image.putalpha(alpha)
        if square:
            canvas = Image.new("RGBA", (size, size))
            canvas.alpha_composite(image, ((size - image.width) // 2, (size - image.height) // 2))
            image = canvas
        fitted.append(image)
    return fitted, True
