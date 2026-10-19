"""Copy the running kernel's own two I2C modules for fast early loading."""
import lzma
import os
from pathlib import Path
import platform

release = platform.release()
root = Path('/lib/modules') / release / 'kernel/drivers/i2c'
destination = Path('/usr/local/lib/stm32-boot') / release
destination.mkdir(parents=True, exist_ok=True)
for relative in ('busses/i2c-bcm2835.ko', 'i2c-dev.ko'):
    source = root / relative
    if source.exists():
        content = source.read_bytes()
    else:
        content = lzma.decompress(Path(str(source) + '.xz').read_bytes())
    assert content.startswith(b'\x7fELF'), source
    target = destination / source.name
    temporary = target.with_suffix('.new')
    temporary.write_bytes(content)
    temporary.chmod(0o644)
    os.replace(temporary, target)
    print(target, len(content))
os.sync()
