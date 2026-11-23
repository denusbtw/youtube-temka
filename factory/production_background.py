"""Original silent 720×1280 background with a six-second looping ball path."""
import math
from tempfile import TemporaryDirectory
from pathlib import Path

from . import config, library, media
from .heavy import HEAVY_LOCK


def create():
    import numpy as np

    config.initialize()
    folder = config.DATA / 'backgrounds'
    folder.mkdir(exist_ok=True)
    target = folder / 'orbit-court.mp4'
    with HEAVY_LOCK:
        if not target.exists():
            with TemporaryDirectory(dir=folder) as temporary:
                root = Path(temporary)
                base = np.empty((1280,720,3),dtype=np.uint8)
                for y in range(1280):
                    intensity = round(10 * math.sin(math.pi * y / 1280))
                    base[y] = (17 + intensity, 33 + intensity, 40 + intensity)
                for y in (445,635,825):
                    base[y:y+4,100:620]=(50,84,90)
                gy,gx=np.ogrid[-27:28,-27:28]
                ball=gx**2+gy**2 <= 25**2
                shine=np.clip((-gx-gy)/95+.2,0,.48)[...,None]
                specular=(gx+9)**2+(gy+10)**2<12
                for frame in range(144):
                    image=base.copy()
                    for lane,color in enumerate(((231,166,103),(139,201,183),(213,145,126))):
                        phase=2*math.pi*(frame/144+lane/3)
                        x=round(360+220*math.cos(phase))
                        floor=445+lane*190
                        y=round(floor-25-100*abs(math.sin(2*phase)))
                        region=image[y-27:y+28,x-27:x+28]
                        shading=np.asarray(color)+(255-np.asarray(color))*shine
                        region[ball]=shading[ball]
                        region[specular]=(248,237,224)
                    (root / f'{frame:03d}.ppm').write_bytes(b'P6\n720 1280\n255\n'+image.tobytes())
                output = root / 'asset.mp4'
                media.run([media.ffmpeg(),'-loglevel','error','-y','-framerate','24','-i',str(root/'%03d.ppm'),
                           '-an','-c:v','libx264','-threads','2','-preset','fast','-crf','22',
                           '-pix_fmt','yuv420p','-movflags','+faststart',str(output)])
                media.validate_output(output,6,expected_audio=False)
                output.replace(target)
        return library.register(target,'backgrounds','CC0-1.0','Original Factory animation','bouncing-balls',{
            'display_name':'Orbit Court · Bouncing Balls','author':'AI Content Factory',
            'license_status':'original_procedural','attribution_required':False,
        })


if __name__ == '__main__':
    print(create())
