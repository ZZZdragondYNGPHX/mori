"""Rebuild nine GIFs from four authored poses each; Pillow is build-time only.

Run build_life_assets.py first. This builder never deforms one static drawing.
"""
import json

from build_life_assets import TARGET, build_base_gifs

if __name__ == '__main__':
    build_base_gifs(json.loads((TARGET / 'clips.json').read_text()))
