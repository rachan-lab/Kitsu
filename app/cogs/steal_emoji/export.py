from __future__ import annotations

import asyncio
import io

import aiohttp

from .constants import GIF_EXPORT_SIZE, MAX_GIF_EXPORT_SIZE, logger
from .image_geometry import fit_frames
from .models import StealItem


async def download_file(url: str) -> bytes:
    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            logger.info(
                "[STEAL] CDN response | status=%s | content_type=%s",
                response.status,
                response.headers.get("Content-Type"),
            )

            if response.status != 200:
                raise RuntimeError(f"Discord CDN returned HTTP {response.status}.")

            return await response.read()


def convert_image(data: bytes, target_format: str, *, max_size: int | None = None) -> bytes:
    try:
        from PIL import Image, ImageSequence
    except ImportError as exc:
        raise RuntimeError(
            "Image conversion requires Pillow. Run: python -m pip install Pillow"
        ) from exc

    output = io.BytesIO()

    with Image.open(io.BytesIO(data)) as image:
        loop = image.info.get("loop", 0)

        # APNG poster images are not part of the animation.
        first_frame = int(bool(image.info.get("default_image")))
        frames, durations = [], []
        for index, frame in enumerate(ImageSequence.Iterator(image)):
            if index < first_frame:
                continue
            frames.append(frame.convert("RGBA"))
            durations.append(frame.info.get("duration", 100) or 100)

        changed = False
        if max_size is not None:
            frames, changed = fit_frames(frames, max_size)
        # Small native GIFs that already fit need no lossy re-encoding.
        if target_format == "GIF" and image.format == "GIF" and not changed:
            return data

        first = frames[0]

        if target_format == "PNG":
            first.save(output, format="PNG")

        elif target_format == "GIF":
            # Sample every frame separately. Compositing them on top of one
            # another drops earlier colors and can collapse an animation.
            palette_source = Image.new("RGB", (64, 64 * len(frames)), "white")
            for index, frame in enumerate(frames):
                sample = frame.copy()
                sample.thumbnail((64, 64), Image.Resampling.LANCZOS)
                palette_source.paste(
                    sample.convert("RGB"),
                    (0, index * 64),
                    mask=sample.getchannel("A"),
                )

            palette = palette_source.quantize(
                colors=255,
                method=Image.Quantize.MEDIANCUT,
            )

            palette_data = (palette.getpalette() + [0] * 768)[:768]
            palette_data[765:768] = [0, 0, 0]

            gif_frames = []

            for frame in frames:
                alpha = frame.getchannel("A")
                # Pillow treats an all-background delta as another GIF header.
                # Give later blank frames a different transparent index so they
                # remain real timed frames after the previous frame is cleared.
                transparent_index = 254 if gif_frames and alpha.getextrema()[1] < 128 else 255
                rgb = Image.new("RGB", frame.size, "white")
                rgb.paste(frame.convert("RGB"), mask=alpha)

                indexed = rgb.quantize(
                    palette=palette,
                    dither=Image.Dither.FLOYDSTEINBERG,
                )

                indexed.putpalette(palette_data)
                indexed.info["transparency"] = transparent_index

                pixels = indexed.load()
                alpha_pixels = alpha.load()

                for y in range(frame.height):
                    for x in range(frame.width):
                        if alpha_pixels[x, y] < 128:
                            pixels[x, y] = transparent_index

                gif_frames.append(indexed)

            gif_frames[0].save(
                output,
                format="GIF",
                save_all=len(gif_frames) > 1,
                append_images=gif_frames[1:],
                duration=durations if len(gif_frames) > 1 else 100,
                loop=loop,
                optimize=False,
                disposal=2,
            )

        elif target_format == "APNG":
            first.save(
                output,
                format="PNG",
                save_all=True,
                append_images=frames[1:],
                duration=durations if len(frames) > 1 else None,
                loop=loop,
                default_image=False,
            )

        elif target_format == "WEBP":
            first.save(
                output,
                format="WEBP",
                save_all=len(frames) > 1,
                append_images=frames[1:],
                duration=durations if len(frames) > 1 else None,
                loop=loop,
            )

        elif target_format == "JPEG":
            background = Image.new("RGB", first.size, "white")
            background.paste(first, mask=first.getchannel("A"))
            background.save(output, format="JPEG", quality=95)

        else:
            raise RuntimeError(f"Unsupported format: {target_format}")

    return output.getvalue()


async def export_item(
    item: StealItem,
    target_format: str,
    *,
    max_size: int = GIF_EXPORT_SIZE,
) -> tuple[bytes, str]:
    if target_format == "GIF" and (type(max_size) is not int or not 1 <= max_size <= MAX_GIF_EXPORT_SIZE):
        raise ValueError(f"Choose a GIF size between 1 and {MAX_GIF_EXPORT_SIZE}px.")
    data = await download_file(item.download_url)

    if target_format == "JSON":
        if item.source_format != "lottie":
            raise RuntimeError("JSON export is only supported for Lottie stickers.")

        return data, "json"

    if item.source_format == "lottie":
        from .lottie import render_lottie

        data = await asyncio.to_thread(render_lottie, data)

    if target_format == "APNG" and item.source_format == "apng":
        return data, "png"

    if target_format == "PNG" and item.source_format == "png":
        return data, "png"

    converted = await asyncio.to_thread(
        convert_image,
        data,
        target_format,
        max_size=max_size if target_format == "GIF" else None,
    )

    extension = {
        "PNG": "png",
        "GIF": "gif",
        "APNG": "png",
        "WEBP": "webp",
        "JPEG": "jpg",
    }[target_format]

    return converted, extension
