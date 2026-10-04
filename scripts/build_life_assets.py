"""Export real four-pose life/work animations, retaining approved artwork."""
import json
import shutil
from pathlib import Path

from build_motion_assets import SIZE, TARGET, export_gif, export_poses
from PIL import Image

PROJECT = Path(__file__).resolve().parents[1]
SOURCE = PROJECT / 'output/life-study-v5'
TIMING = {'nothing': [2200,120,1400,1000], 'eat': [440,500,640,500],
          'think': [440,760,760,600], 'attention': [600,600,600,800],
          'cheerup': [320,560,720,800], 'true': [320,560,680,800],
          'wrong': [360,640,720,900], 'touch': [220,440,560,800],
          'pickup': [200,360,360,500], 'daily-stretch': [280,600,720,440],
          'daily-nap': [520,1400,1300,780]}
FAMILIES = tuple('base-'+k for k in TIMING if k not in ('touch','pickup') and not k.startswith('daily-')) + ('daily-stretch','daily-nap','window-peek-top')


def build_base_gifs(manifest):
    import sys
    sys.path.insert(0,str(PROJECT/'src'))
    from character.defaults import GIF_ANIMATIONS
    (SOURCE/'gifs').mkdir(parents=True,exist_ok=True)
    for key,relative in GIF_ANIMATIONS.items():
        name='base-'+key
        clip=manifest['clips'][name]
        frames=[Image.open(TARGET/f).convert('RGBA') for f in clip['frames']]
        export_gif(name,{**clip,'poses':list(range(4))},frames,SOURCE)
        target=PROJECT/'src/asset_library'/relative
        backup=SOURCE/'original-gifs'/relative
        if target.is_file() and not backup.exists():
            backup.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(target,backup)
        shutil.copyfile(SOURCE/'gifs'/(name+'.gif'),target)


def build():
    (SOURCE/'gifs').mkdir(parents=True,exist_ok=True)
    poses={family:export_poses(family,SOURCE) for family in FAMILIES}
    for family in ('head','pickup'):
        poses[family]=[Image.open(TARGET/f'{family}-{i}.png').convert('RGBA') for i in range(4)]
    clips={}
    for key,durations in TIMING.items():
        name=key if key.startswith('daily-') else 'base-'+key
        family='head' if key=='touch' else 'pickup' if key=='pickup' else name
        clips[name]={'family':family,'poses':[0,1,2,3],'durations':durations,
                     'loop':key in ('nothing','eat','think','attention','pickup')}
    clips['window-peek-top']={'family':'window-peek-top','poses':[0,1,2,3,2,1,0],
                              'durations':[480,640,1280,1040,1280,1200,1080],'loop':False}
    anchors=[[141,215],[142,214],[148,217],[145,215]]
    for name,clip in clips.items():
        export_gif(name,clip,poses[clip['family']],SOURCE)
        if name=='window-peek-top':
            clip['anchors']=[anchors[i] for i in clip['poses']]
        clip['frames']=[f"{clip['family']}-{i}.png" for i in clip.pop('poses')]
    path=TARGET/'clips.json'
    manifest=json.loads(path.read_text())
    manifest['clips'].update(clips)
    manifest['base_animations']={k:'base-'+k for k in TIMING if not k.startswith('daily-')}
    path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    build_base_gifs(manifest)
    preview={'canvas':list(SIZE),'clips':manifest['clips'], 'support':manifest['window_support'],
             'contact':manifest['window_button_contact'],'stride':manifest['walk_stride_pixels'],
             'sway':manifest['walk_sway_degrees'],'base':manifest['base_animations'],'transition_ms':120}
    preview['bounds'] = {}
    for filename in {f for clip in manifest['clips'].values() for f in clip['frames']}:
        with Image.open(TARGET / filename) as image:
            preview['bounds'][filename] = image.getchannel('A').point(lambda a: 255 if a >= 32 else 0).getbbox()
    (PROJECT/'docs/life-preview-data.js').write_text('window.MORI_LIFE = '+json.dumps(preview,ensure_ascii=False)+';\n')
    print('Exported 40 authored PNGs, 12 clips and nine genuine four-frame base GIFs.')


if __name__=='__main__':
    build()
