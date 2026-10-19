"""Changed-page writes for the installed 64x128 SH1107 rotated to 128x64."""
from boot_display import pack_pages

_BINARY = [0] + [1] * 255


def display_rotated_frame(device, image):
    assert image.mode == "1" and image.size == (128, 64)
    # Pillow expands packed pixels in C; packing then uses the verified boot
    # driver's page order instead of luma's Python loop over all 8192 pixels.
    pages = pack_pages(image.convert("L").point(_BINARY).tobytes())
    previous = getattr(device, "_cached_pages", None)
    for page, data in enumerate(pages):
        if previous is None or data != previous[page]:
            device.command(0x10, 0x00, 0xb0 | page)
            device.data(list(data))
    # Publish only after every write succeeds; a failed frame will be retried.
    device._cached_pages = pages
