"""Render Discord's vector stickers before exporting or uploading as emoji."""

import io
import json
import math

from .constants import STICKER_SIZE


def render_lottie(data: bytes, *, size: int = STICKER_SIZE) -> bytes:
    from rlottie_python import LottieAnimation

    try:
        if len(data) > 512 * 1024:
            raise ValueError
        document = json.loads(data)
        width, height = int(document["w"]), int(document["h"])
        rate = float(document["fr"])
        length = float(document["op"]) - float(document.get("ip", 0))
        if not (1 <= width <= 4096 and 1 <= height <= 4096 and 0 < rate <= 120 and 0 < length / rate <= 5):
            raise ValueError
        # Discord stickers are self-contained. Do not resolve local/network
        # resource paths supplied by a JSON asset.
        for asset in document.get("assets", []):
            if asset.get("u") or (asset.get("p") and not str(asset["p"]).startswith("data:image/")):
                raise ValueError
        if any(font.get("fPath") for font in document.get("fonts", {}).get("list", [])):
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
        raise ValueError("I couldn't render this Lottie sticker. Use a self-contained animation up to 5 seconds long.") from error

    scale = min(1.0, size / max(width, height))
    dimensions = (max(1, round(width * scale)), max(1, round(height * scale)))
    with LottieAnimation.from_data(data.decode("utf-8")) as animation:
        if not animation.animation_p:
            raise ValueError("I couldn't render this Lottie sticker.")
        total = animation.lottie_animation_get_totalframe()
        if not 1 <= total <= 600:
            raise ValueError("I couldn't render this Lottie sticker.")
        duration = length / rate
        count = max(1, math.ceil(duration * min(rate, 50)))
        # GIF stores time in 10ms units. Round cumulative boundaries so
        # fractional source rates don't make the exported animation run faster.
        boundaries = [round(index * duration * 100 / count) * 10 for index in range(count + 1)]
        frames = [animation.render_pillow_frame(
            frame_num=min(total - 1, index * total // count), width=dimensions[0], height=dimensions[1],
        ).copy() for index in range(count)]
    output = io.BytesIO()
    frames[0].save(
        output, format="PNG", save_all=count > 1, append_images=frames[1:],
        duration=[max(10, end - start) for start, end in zip(boundaries, boundaries[1:])],
        loop=0, disposal=0, blend=0,
    )
    return output.getvalue()
