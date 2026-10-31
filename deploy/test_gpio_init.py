"""Exercise delayed GPIO availability and once-per-boot behavior in a fake sysfs."""
from pathlib import Path
import subprocess
import tempfile
import threading
import time

root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    base=Path(directory)
    gpio=base/'gpio'
    gpio.mkdir()
    (gpio/'export').touch()
    script=(root/'deploy/system/board-usb-gpio-init.sh').read_text().replace('/sys/class/gpio',str(gpio)).replace('/run/board-usb-gpio-init',str(base/'init'))
    p=base/'init.sh'
    p.write_text(script)
    def ready():
        for number in (142,118):
            d=gpio/f'gpio{number}'
            d.mkdir()
            (d/'direction').touch()
            (d/'value').write_text('1')
    timer=threading.Timer(.5,ready)
    timer.start()
    try:
        subprocess.run(['bash',str(p)],check=True,timeout=5)
        assert (base/'init.done').exists()
        assert (gpio/'gpio118/value').read_text().strip()=='1'
        (gpio/'gpio118/direction').write_text('unchanged')
        subprocess.run(['bash',str(p)],check=True,timeout=3)
        assert (gpio/'gpio118/direction').read_text()=='unchanged'
    finally:
        timer.join()
print('PASS delayed GPIO export and once-per-boot initialization; no real GPIO touched')
