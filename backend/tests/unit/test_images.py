"""Photos for a model provider: accepted types as they are, the rest as JPEG, and a gallery that fits one request."""

import base64
import os
from io import BytesIO

from PIL import Image

from app.ai.images import ANTHROPIC_IMAGES, GEMINI_IMAGES, ImageLimits, _share, fit_images
from tests.photos import jpeg


def noisy(size: tuple[int, int] = (1200, 900), seed: int = 0, fmt: str = "JPEG") -> bytes:
    """Hard to compress, like a detailed photo: its encoded size is large and shrinks only with the picture."""
    img = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
    out = BytesIO()
    img.save(out, fmt, **({"quality": 95} if fmt == "JPEG" else {}))
    return out.getvalue()


def decode(item: tuple[str, str] | None) -> tuple[str, bytes]:
    assert item is not None
    return item[0], base64.b64decode(item[1])


def test_a_small_photo_of_an_accepted_type_goes_untouched() -> None:
    data = jpeg(1, (300, 200))
    assert decode(fit_images([(data, "image/jpeg")], GEMINI_IMAGES)[0]) == ("image/jpeg", data)
    assert decode(fit_images([(data, None)], GEMINI_IMAGES)[0]) == (
        "image/jpeg",
        data,
    )  # the type is read from it
    png = jpeg(2, (300, 200), "PNG")
    assert decode(fit_images([(png, "image/png")], GEMINI_IMAGES)[0]) == ("image/png", png)


def test_a_gif_becomes_a_jpeg_for_gemini_and_stays_a_gif_for_anthropic() -> None:
    gif = jpeg(3, (400, 300), "GIF")
    media, data = decode(fit_images([(gif, "image/gif")], GEMINI_IMAGES)[0])
    assert media == "image/jpeg" and data[:2] == b"\xff\xd8"
    with Image.open(BytesIO(data)) as img:
        assert img.format == "JPEG" and img.size == (400, 300)
    assert decode(fit_images([(gif, "image/gif")], ANTHROPIC_IMAGES)[0]) == ("image/gif", gif)


def test_the_type_is_the_one_the_photo_has_when_the_label_is_not_one_the_provider_takes() -> None:
    png = jpeg(4, (200, 100), "PNG")
    assert decode(fit_images([(png, "image/bmp")], GEMINI_IMAGES)[0]) == ("image/png", png)  # sniffed
    only_jpeg = ImageLimits(("image/jpeg",))
    assert decode(fit_images([(png, "image/png")], only_jpeg)[0])[0] == "image/jpeg"  # not taken: converted


def test_a_big_photo_is_shrunk_to_the_model_side_and_transparency_goes_on_white() -> None:
    png = Image.new("RGBA", (400, 300), (0, 0, 0, 0))  # fully transparent
    out = BytesIO()
    png.save(out, "PNG")
    media, data = decode(fit_images([(out.getvalue(), "image/png")], GEMINI_IMAGES, max_side=100)[0])
    with Image.open(BytesIO(data)) as img:
        assert media == "image/jpeg" and max(img.size) == 100
        pixel = img.convert("RGB").getpixel((50, 40))
        assert isinstance(pixel, tuple) and min(pixel) > 240  # white, not black


def test_the_orientation_of_a_phone_photo_survives_the_shrinking() -> None:
    img = Image.new("RGB", (200, 100), (200, 30, 30))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees to display
    out = BytesIO()
    img.save(out, "JPEG", exif=exif)
    _, data = decode(fit_images([(out.getvalue(), "image/jpeg")], GEMINI_IMAGES, max_side=60)[0])
    with Image.open(BytesIO(data)) as shrunk:
        assert shrunk.size[1] > shrunk.size[0]  # portrait, as the phone showed it


def test_what_cannot_be_decoded_is_sent_only_when_its_type_is_taken() -> None:
    assert fit_images([(b"junk", "image/gif")], GEMINI_IMAGES) == [None]
    assert decode(fit_images([(b"junk", "image/heic")], GEMINI_IMAGES)[0]) == ("image/heic", b"junk")
    assert fit_images([(b"junk", None)], GEMINI_IMAGES) == [None]


def test_a_gallery_over_the_request_cap_is_shrunk_step_by_step_and_small_photos_are_left_alone() -> None:
    small = jpeg(9, (64, 64))
    big = [noisy(seed=i) for i in range(6)]
    blobs = [(small, "image/jpeg"), *[(b, "image/jpeg") for b in big]]
    raw_total = sum(len(b) for b, _ in blobs) * 4 // 3
    budget = 3_000_000
    limits = ImageLimits(("image/jpeg",), budget=budget)
    assert raw_total > budget  # it would not fit as it is
    out = fit_images(blobs, limits)
    sizes = [len(x[1]) for x in out if x]
    assert len(sizes) == 7 and sum(sizes) <= budget  # base64 length is what counts against the cap
    assert decode(out[0]) == ("image/jpeg", small)  # under its share: untouched
    assert all(decode(x)[0] == "image/jpeg" for x in out)
    # Nothing is shrunk when it fits.
    roomy = fit_images(blobs, ImageLimits(("image/jpeg",), budget=raw_total + 100))
    assert [decode(x)[1] for x in roomy] == [b for b, _ in blobs]


def test_a_full_twenty_photo_gallery_fits_geminis_inline_cap() -> None:
    blobs = [(noisy((1400, 1000), seed=i), "image/jpeg") for i in range(20)]
    assert sum(len(b) for b, _ in blobs) * 4 // 3 > 20 * 1024 * 1024  # more than Gemini would take
    out = fit_images(blobs, GEMINI_IMAGES)
    assert GEMINI_IMAGES.budget is not None and sum(len(x[1]) for x in out if x) <= GEMINI_IMAGES.budget
    assert all(x is not None for x in out)


def test_the_budget_a_small_photo_leaves_goes_to_the_big_ones() -> None:
    assert _share([10, 100, 100], 150) == 70  # not 50
    assert _share([100, 100, 100], 150) == 50
    assert _share([10, 20, 30], 1000) == 333  # everything fits: nothing to shrink
